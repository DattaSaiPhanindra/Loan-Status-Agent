"""OpenRouter LLM client with tool calling for discovery."""

from __future__ import annotations

import time

from openai import OpenAI

from agent.surface import Observation
from config import Settings

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "perform_action",
            "description": "Perform an action on the UI surface.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": ["click", "type", "select", "navigate", "wait", "scroll"],
                        "description": "Type of action to perform",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Element reference from the observation (e.g. 'e3').",
                    },
                    "value": {
                        "type": "string",
                        "description": "Value: text to type, URL to navigate, option to select, seconds to wait.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what this action accomplishes.",
                    },
                },
                "required": ["action_type", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_complete",
            "description": "Call when the goal is fully achieved. Extract requested data from the current page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Summary of what was accomplished",
                    },
                    "extracted_data": {
                        "type": "object",
                        "description": "Key-value pairs of extracted data",
                    },
                },
                "required": ["summary", "extracted_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_stuck",
            "description": "Call when you cannot proceed — unachievable goal, unrecoverable error, or looping.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why you're stuck",
                    },
                },
                "required": ["reason"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are an AI agent automating a legacy banking back-office application. You observe the current UI state and decide what action to take next to achieve the given goal.

RULES:
1. You can only interact with elements visible in the current observation.
2. Use element refs (e0, e1, etc.) from the observation to target actions.
3. For text input, use action_type "type" with the ref of the input field and the value to enter.
4. For clicking buttons or links, use action_type "click" with the ref.
5. For navigation, use action_type "navigate" with the URL as value.
6. After each action, you'll receive a new observation. Assess progress toward the goal.
7. When the goal is achieved and you can see the requested data on screen, call mark_complete with extracted data.
8. If stuck, call mark_stuck with a clear reason.
9. Be efficient — don't repeat actions that didn't work. Try alternatives.
10. READ the page content carefully. The raw_text contains visible text you can use to extract data.
11. You are operating on a LEGACY app with no modern UI. Elements are identified by name attributes and text content, not IDs.
12. When you need to log in, use username "agent" and password "agent" — these are mock credentials for automation.
13. IMPORTANT: When you see the data you need on the page (in raw_text), call mark_complete immediately with the extracted data. Don't keep navigating.
14. ALWAYS call exactly one tool per turn. Never respond with plain text only.
"""


class LLMClient:
    def __init__(self):
        settings = Settings()
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
        )
        self.model = "nvidia/nemotron-3-super-120b-a12b:free"
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def reset(self):
        """Clear conversation history."""
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def decide(self, goal: str, observation: Observation, step_number: int) -> dict:
        """Given a goal and current observation, return the next action or completion signal."""
        elements_desc = "\n".join(
            f"  {el.ref}: [{el.role}] name={el.name!r} value={el.value!r}"
            for el in observation.elements
        )

        user_content = f"""GOAL: {goal}

STEP: {step_number}
CURRENT URL: {observation.url}

INTERACTIVE ELEMENTS:
{elements_desc if elements_desc else "  (no interactive elements found)"}

PAGE TEXT (first 2000 chars):
{observation.raw_text[:2000]}

Decide your next action. If you can see the requested data on the page, call mark_complete with the extracted data. You MUST call one of the tools."""

        self.messages.append({"role": "user", "content": user_content})

        max_retries = 5
        response = None
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    temperature=0.0,
                    max_tokens=1024,
                    timeout=60,
                )
                break
            except Exception as e:
                err_str = str(e).lower()
                retryable = any(
                    k in err_str
                    for k in [
                        "503",
                        "504",
                        "unavailable",
                        "429",
                        "disconnected",
                        "timeout",
                        "deadline",
                        "connection",
                        "reset",
                        "broken pipe",
                        "rate",
                        "overloaded",
                    ]
                )
                if retryable:
                    wait = 2 ** attempt
                    print(
                        f"  Retry {attempt + 1}/{max_retries} after {wait}s: {str(e)[:80]}"
                    )
                    time.sleep(wait)
                else:
                    raise
        if response is None:
            raise RuntimeError("LLM unavailable after retries")

        message = response.choices[0].message

        self.messages.append(message.model_dump())

        if message.tool_calls:
            tc = message.tool_calls[0]
            import json

            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "Action acknowledged. Next observation will follow.",
                }
            )

            return {"tool": tc.function.name, "args": args}

        text_response = message.content or "No response"
        return {
            "tool": "mark_stuck",
            "args": {
                "reason": f"Model did not call a tool. Response: {text_response[:200]}"
            },
        }


if __name__ == "__main__":
    import os

    os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
    client = LLMClient()
    print(f"LLM client OK: model={client.model}")
    print(f"Tools: {[t['function']['name'] for t in TOOLS]}")
