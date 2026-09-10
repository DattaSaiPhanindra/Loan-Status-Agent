"""Google Gemini client with function calling for discovery."""

from __future__ import annotations

import time

from google import genai
from google.genai import types

from agent.surface import Observation
from config import Settings

TOOL_DECLARATIONS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="perform_action",
            description="Perform an action on the UI surface.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "action_type": types.Schema(
                        type="STRING",
                        enum=["click", "type", "select", "navigate", "wait", "scroll"],
                        description="Type of action to perform",
                    ),
                    "ref": types.Schema(
                        type="STRING",
                        description="Element reference from the observation (e.g. 'e3').",
                    ),
                    "value": types.Schema(
                        type="STRING",
                        description="Value: text to type, URL to navigate, option to select, seconds to wait.",
                    ),
                    "description": types.Schema(
                        type="STRING",
                        description="Brief description of what this action accomplishes.",
                    ),
                },
                required=["action_type", "description"],
            ),
        ),
        types.FunctionDeclaration(
            name="mark_complete",
            description="Call when the goal is fully achieved. Extract requested data from the current page.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "summary": types.Schema(
                        type="STRING", description="Summary of what was accomplished"
                    ),
                    "extracted_data": types.Schema(
                        type="OBJECT", description="Key-value pairs of extracted data"
                    ),
                },
                required=["summary", "extracted_data"],
            ),
        ),
        types.FunctionDeclaration(
            name="mark_stuck",
            description="Call when you cannot proceed — unachievable goal, unrecoverable error, or looping.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "reason": types.Schema(
                        type="STRING", description="Why you're stuck"
                    ),
                },
                required=["reason"],
            ),
        ),
    ]
)

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
14. ALWAYS call exactly one function per turn. Never respond with plain text — always use perform_action, mark_complete, or mark_stuck.
"""


class LLMClient:
    def __init__(self):
        settings = Settings()
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = "gemini-3.5-flash"
        self.history: list[types.Content] = []

    def reset(self):
        """Clear conversation history."""
        self.history = []

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

Decide your next action. If you can see the requested data on the page, call mark_complete with the extracted data. You MUST call one of the functions."""

        self.history.append(
            types.Content(role="user", parts=[types.Part.from_text(text=user_content)])
        )

        max_retries = 5
        response = None
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=self.history,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        tools=[TOOL_DECLARATIONS],
                        temperature=0.0,
                        http_options=types.HttpOptions(timeout=30_000),
                    ),
                )
                break
            except Exception as e:
                err_str = str(e).lower()
                retryable = any(k in err_str for k in ["503", "unavailable", "429", "disconnected", "timeout", "connection", "reset", "broken pipe"])
                if retryable:
                    wait = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
                    print(f"  Retry {attempt + 1}/{max_retries} after {wait}s: {str(e)[:80]}")
                    time.sleep(wait)
                else:
                    raise
        if response is None:
            raise RuntimeError("LLM unavailable after retries")

        if response.candidates and response.candidates[0].content:
            self.history.append(response.candidates[0].content)

        for part in response.candidates[0].content.parts:
            if part.function_call:
                fc = part.function_call
                args = dict(fc.args) if fc.args else {}

                self.history.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=fc.name,
                                response={
                                    "status": "ok",
                                    "message": "Action acknowledged.",
                                },
                            )
                        ],
                    )
                )

                return {"tool": fc.name, "args": args}

        text_response = ""
        for part in response.candidates[0].content.parts:
            if part.text:
                text_response += part.text
        return {
            "tool": "mark_stuck",
            "args": {
                "reason": f"Model did not call a function. Response: {text_response[:200]}"
            },
        }


if __name__ == "__main__":
    import os

    os.environ.setdefault("GEMINI_API_KEY", "test-key")
    client = LLMClient()
    print(f"LLM client OK: model={client.model}")
    print("Tools: perform_action, mark_complete, mark_stuck")
