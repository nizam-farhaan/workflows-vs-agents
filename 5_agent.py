"""
5_agent.py  -  "I was charged twice" support agent (ReAct)

Read 3_agent_example.md first.

The idea: the LLM decides the next step and when to stop. YOUR CODE runs the tools,
sets the limits, and asks a human before any money moves.

    Thought -> Action (tool call) -> Observation (tool result) -> repeat -> Final answer

Run:  pip install -r requirements.txt
      export GEMINI_API_KEY="your-key"      (or the script asks for it)
      python 5_agent.py
"""

import json
import os
from getpass import getpass

from google import genai
from google.genai import types

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or getpass("Gemini API key: "))
MAX_STEPS = 8  # guard 1: the loop always ends

# ---------------------------------------------------------------- mock company systems
ORDERS = {
    "B2001": {"amount": 2499, "status": "delivered"},
    "B2002": {"amount": 1299, "status": "delivered"},
}
PAYMENTS = {  # B2001: a real double charge. B2002: second charge failed and was reversed.
    "B2001": [{"id": "P11", "amount": 2499, "status": "success", "time": "10:02"},
              {"id": "P12", "amount": 2499, "status": "success", "time": "10:03"}],
    "B2002": [{"id": "P21", "amount": 1299, "status": "success", "time": "18:40"},
              {"id": "P22", "amount": 1299, "status": "failed_auto_reversed", "time": "18:41"}],
}
REFUNDS = {"B2001": [], "B2002": []}


# ---------------------------------------------------------------- tools (plain Python)
def get_order(order_id: str) -> dict:
    return ORDERS.get(order_id, {"error": f"No order {order_id}. Check the ID."})


def get_payments(order_id: str) -> list | dict:
    return PAYMENTS.get(order_id, {"error": f"No payments for {order_id}."})


def get_refunds(order_id: str) -> list | dict:
    return REFUNDS.get(order_id, {"error": f"No refund record for {order_id}."})


def issue_refund(order_id: str, payment_id: str, amount: int) -> dict:
    # guard 2: CODE checks the request against the real records. Never trust the LLM's numbers.
    payment = next((p for p in PAYMENTS.get(order_id, []) if p["id"] == payment_id), None)
    if payment is None:
        return {"error": f"Payment {payment_id} is not part of order {order_id}."}
    if payment["status"] != "success":
        return {"error": f"Payment {payment_id} did not succeed, so there is nothing to refund."}
    if amount != payment["amount"]:
        return {"error": f"Amount must be {payment['amount']}, the amount of payment {payment_id}."}
    if any(r["payment_id"] == payment_id for r in REFUNDS.get(order_id, [])):
        return {"error": f"Payment {payment_id} is already refunded."}

    # guard 3: a human approves every money action. The LLM can only ASK.
    answer = input(f"\n  APPROVAL NEEDED: refund Rs {amount} for payment {payment_id}? (y/n): ")
    if answer.strip().lower() != "y":
        return {"status": "rejected_by_human"}
    REFUNDS.setdefault(order_id, []).append({"payment_id": payment_id, "amount": amount})
    return {"status": "refund_started", "payment_id": payment_id, "amount": amount}


TOOLS = {"get_order": get_order, "get_payments": get_payments,
         "get_refunds": get_refunds, "issue_refund": issue_refund}


def declare(name: str, description: str, props: dict) -> types.FunctionDeclaration:
    """Tool schema the LLM reads. The description IS a prompt: write it well."""
    return types.FunctionDeclaration(
        name=name, description=description,
        parameters_json_schema={"type": "object", "properties": props, "required": list(props)},
    )


ID = {"type": "string", "description": "Order ID, for example B2001"}
DECLARATIONS = [
    declare("get_order", "Get the order amount and status.", {"order_id": ID}),
    declare("get_payments", "List every payment attempt for an order, with status.", {"order_id": ID}),
    declare("get_refunds", "List refunds already issued for an order.", {"order_id": ID}),
    declare("issue_refund", "Refund ONE payment. Use only for a confirmed duplicate charge that is not refunded yet.",
            {"order_id": ID, "payment_id": {"type": "string"}, "amount": {"type": "integer"}}),
]

SYSTEM = """You are a billing support agent. Investigate before you act.
Before each tool call, write one short line that starts with 'Thought:'.
Use tools to check facts. Never guess amounts or IDs.
When you are done, reply to the customer in 2-3 short sentences."""


# ---------------------------------------------------------------- the ReAct loop
def run_agent(ticket: str) -> str:
    print("\n" + "=" * 70 + f"\nCUSTOMER: {ticket}")
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        tools=[types.Tool(function_declarations=DECLARATIONS)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # WE run the loop
        temperature=0,
    )
    history = [types.Content(role="user", parts=[types.Part.from_text(text=ticket)])]
    seen_calls = set()

    for step in range(1, MAX_STEPS + 1):
        response = client.models.generate_content(model=MODEL, contents=history, config=config)
        content = response.candidates[0].content if response.candidates else None
        if content is None:  # blocked or empty response: do not guess, hand over
            return "The model returned no answer. Handing this ticket to a human."
        history.append(content)  # keep the model's Thought + Action

        calls = response.function_calls
        if not calls:  # no tool call = the LLM decided it is done
            return response.text or "The model returned no answer. Handing this ticket to a human."

        print(f"\n  --- step {step} ---")
        for part in content.parts or []:
            if part.text:
                print(f"  THOUGHT:     {part.text.strip().removeprefix('Thought:').strip()}")

        results = []
        for call in calls:
            args = dict(call.args or {})
            print(f"  ACTION:      {call.name}({args})")
            key = (call.name, json.dumps(args, sort_keys=True))
            if key in seen_calls:  # guard 4: stop repeat loops
                result = {"error": "You already made this exact call. Use the earlier result."}
            elif call.name not in TOOLS:
                result = {"error": f"Unknown tool {call.name}."}
            else:
                seen_calls.add(key)
                try:
                    result = TOOLS[call.name](**args)
                except Exception as e:  # guard 5: tool errors go back to the LLM, no crash
                    result = {"error": str(e)}
            print(f"  OBSERVATION: {result}")
            results.append(types.Part.from_function_response(name=call.name, response={"result": result}))
        history.append(types.Content(role="user", parts=results))  # the LLM sees the results next turn

    return "Step limit reached. Handing this ticket to a human."


if __name__ == "__main__":
    for ticket in ["I was charged twice for order B2001. Please fix this.",
                   "My bank shows two payments for order B2002. Did you charge me twice?"]:
        print(f"\n  FINAL REPLY: {run_agent(ticket)}")
