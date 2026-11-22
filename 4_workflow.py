"""
4_workflow.py  -  Support ticket triage as a WORKFLOW

Read 2_workflow_example.md first.

The idea: YOUR CODE decides every step. The LLM only does the work inside a step.

    Ticket -> 1. Classify (LLM) -> 2. Route (code) -> 3. Draft reply (LLM) -> 4. Check (code) -> Reply
                                                                      ^                |
                                                                      +-- fix, max 2 --+

Patterns used:  routing (step 2), prompt chaining (steps 1 -> 3), evaluator-optimizer (step 4)

Run:  pip install -r requirements.txt
      copy .env.example to .env and paste your key there   (or the script asks for it)
      python 4_workflow.py
"""

import json
import os
from getpass import getpass

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()  # reads GEMINI_API_KEY from the .env file, if there is one
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"  # change in one place
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or getpass("Gemini API key: "))


def ask_llm(prompt: str, as_json: bool = False) -> str:
    """One LLM call. That is all the LLM does in a workflow: fill one step."""
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json" if as_json else "text/plain",
    )
    return client.models.generate_content(model=MODEL, contents=prompt, config=config).text


# ---------------------------------------------------------------- mock company data
ORDERS = {"A1023": "Shipped on Monday. Out for delivery today. Courier: BlueDart."}
REFUND_POLICY = "Refunds are allowed within 7 days of delivery for unused items. Pickup is free."
HELP_ARTICLE = "App crash on photo upload: update the app to the latest version, then clear the app cache."

TICKETS = [
    "Where is my order A1023? It is late.",
    "I want to return the shoes from order A1077, wrong size.",
    "The app crashes every time I upload a profile photo.",
    "I want to sell my products on your website. Who do I talk to?",
]


# ---------------------------------------------------------------- STEP 1: classify (LLM)
def classify(ticket: str) -> dict:
    prompt = f"""Classify this support ticket.
Return JSON: {{"category": one of ["order_status", "refund", "technical", "other"], "order_id": string or null}}
Ticket: {ticket}"""
    return json.loads(ask_llm(prompt, as_json=True))


# ---------------------------------------------------------------- STEP 2: route (CODE decides)
def get_context(label: dict) -> str | None:
    """A plain dict lookup. No LLM here. This is what makes it a workflow."""
    routes = {
        "order_status": lambda: ORDERS.get(label.get("order_id") or "", "Order not found."),
        "refund": lambda: REFUND_POLICY,
        "technical": lambda: HELP_ARTICLE,
        "other": lambda: None,  # None = send to a human
    }
    return routes.get(label.get("category"), lambda: None)()  # unknown label -> human


# ---------------------------------------------------------------- STEP 3: draft (LLM)
def draft_reply(ticket: str, context: str, feedback: str = "") -> str:
    prompt = f"""You are a support agent. Write a short, polite reply.
Use ONLY the facts below. Do not promise anything that is not in the facts.
Facts: {context}
Ticket: {ticket}
{f"Fix these problems from your last draft: {feedback}" if feedback else ""}"""
    return ask_llm(prompt).strip()


# ---------------------------------------------------------------- STEP 4: check (CODE gate)
BANNED = ["guarantee", "100%", "refund has been processed", "compensation"]


def check_reply(reply: str) -> list[str]:
    """Rules in code. Cheap, fast, and the same every time."""
    problems = []
    if len(reply.split()) > 80:
        problems.append(f"Too long ({len(reply.split())} words). Keep it under 80 words.")
    for phrase in BANNED:
        if phrase in reply.lower():
            problems.append(f"Remove the promise '{phrase}'.")
    return problems


# ---------------------------------------------------------------- the workflow
def handle_ticket(ticket: str) -> None:
    print("\n" + "=" * 70 + f"\nTICKET: {ticket}")

    label = classify(ticket)
    print(f"  STEP 1  classify -> {label}")

    context = get_context(label)
    if context is None:
        print("  STEP 2  route    -> human team (no automatic reply)")
        return
    print(f"  STEP 2  route    -> {label['category']} path | facts: {context}")

    feedback = ""
    for attempt in range(1, 4):  # 1 draft + max 2 fixes. CODE owns this loop.
        reply = draft_reply(ticket, context, feedback)
        problems = check_reply(reply)
        print(f"  STEP 3  draft #{attempt}: {reply}")
        if not problems:
            print("  STEP 4  check    -> PASS")
            return
        feedback = " ".join(problems)
        print(f"  STEP 4  check    -> FAIL: {feedback}")
    print("  RESULT  -> still failing after 2 fixes. Send to a human.")


if __name__ == "__main__":
    for t in TICKETS:
        handle_ticket(t)
