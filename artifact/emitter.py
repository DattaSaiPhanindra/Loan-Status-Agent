"""Convert a discovery run into a reusable CapabilityArtifact."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from agent.loop import DiscoveryResult, StepRecord
from artifact.schema import (
    CapabilityArtifact,
    Checkpoint,
    InputParameter,
    LocatorStrategy,
    OutputField,
    StepAction,
    StepDefinition,
)


def build_locator_from_step(record: StepRecord) -> LocatorStrategy | None:
    """Build a LocatorStrategy from a discovery step's observation and action."""
    args = record.decision.get("args", {})
    ref = args.get("ref", "")
    if not ref:
        return None

    # Find the element in the observation
    element = None
    for el in record.observation.elements:
        if el.ref == ref:
            element = el
            break

    if not element:
        return None

    role = element.role
    name = element.name

    # Build primary selector based on what we know
    primary = ""
    fallbacks = []

    # For inputs: name attribute is most stable on legacy apps
    if role == "textbox" and name:
        primary = f'input[name="{name}"]'
        fallbacks.append(f'textarea[name="{name}"]')
    elif role == "button":
        if name:
            primary = f'input[type="submit"][value="{name}"]'
            fallbacks.append(f'button:has-text("{name}")')
    elif role == "link":
        if name:
            primary = f'a:has-text("{name}")'
    elif role == "combobox" and name:
        primary = f'select[name="{name}"]'

    # Generic fallback using role + name
    if not primary:
        primary = f'[role="{role}"]'
        if name:
            fallbacks.append(f':has-text("{name}")')

    return LocatorStrategy(
        primary=primary,
        fallbacks=fallbacks,
        role=role,
        name=name,
        description=args.get("description", ""),
    )


def detect_parameterized_value(action_args: dict, goal: str) -> tuple[bool, str]:
    """Detect if a typed value is likely a parameter (e.g. application ID).

    Returns (is_parameterized, param_key).
    """
    value = action_args.get("value", "")
    action_type = action_args.get("action_type", "")
    description = action_args.get("description", "").lower()

    if action_type != "type":
        return False, ""

    # Skip login credentials — those are fixed, not parameterized
    if value in ("agent",) or "username" in description or "password" in description:
        return False, ""

    # Detect application ID patterns
    if any(
        k in description
        for k in ["application", "loan", "id", "search", "look up", "member"]
    ):
        return True, "application_id"

    # If the value looks like an ID (has digits and/or hyphens)
    if any(c.isdigit() for c in value) and len(value) <= 20:
        return True, "application_id"

    return False, ""


def emit_artifact(
    result: DiscoveryResult,
    name: str = "check_loan_application_status",
    description: str = "Look up a loan application and extract its status, missing documents, and next steps",
    target_url: str = "http://127.0.0.1:8080",
) -> CapabilityArtifact:
    """Convert a successful DiscoveryResult into a CapabilityArtifact."""
    if not result.success:
        raise ValueError(f"Cannot emit artifact from failed discovery: {result.error}")

    steps: list[StepDefinition] = []
    has_parameterized = False

    for record in result.steps:
        args = record.decision.get("args", {})
        tool = record.decision.get("tool", "")

        # Skip the final mark_complete step — it's not a UI action
        if tool != "perform_action":
            continue

        action_type = args.get("action_type", "")
        value = args.get("value", "")
        desc = args.get("description", "")

        # Check if this value should be parameterized
        is_param, param_key = detect_parameterized_value(args, result.goal)
        if is_param:
            has_parameterized = True

        # Build locator
        locator = build_locator_from_step(record)

        # Determine risk level
        risk = "read"
        if action_type in ("navigate", "wait", "scroll"):
            risk = "safe"

        # Build checkpoint based on URL change or page content
        checkpoint = None
        # For click actions that navigate, check URL changed
        if action_type == "click" and locator:
            checkpoint = Checkpoint(
                check_type="url_contains",
                value=record.observation.url.split("/")[-1]
                if "/" in record.observation.url
                else "",
                description=f"Page should reflect action: {desc}",
            )

        step = StepDefinition(
            step_index=len(steps),
            description=desc,
            action=StepAction(
                action_type=action_type,
                locator=locator,
                value="" if is_param else value,
                is_parameterized=is_param,
                param_key=param_key if is_param else "",
            ),
            checkpoint=checkpoint,
            is_critical=True,
            risk_level=risk,
        )
        steps.append(step)

    # Define inputs
    inputs = []
    if has_parameterized:
        inputs.append(
            InputParameter(
                name="application_id",
                description="The loan application ID to look up (e.g. LN-1042)",
                param_type="string",
                required=True,
                example="LN-1042",
            )
        )

    # Define outputs from extracted data
    outputs = []
    for key, value in result.extracted_data.items():
        field_type = "list" if isinstance(value, list) else "string"
        outputs.append(
            OutputField(
                name=key,
                description=f"Extracted {key.replace('_', ' ')} from application detail page",
                field_type=field_type,
            )
        )

    # Build success checkpoint from final observation
    last_action_step = None
    for record in reversed(result.steps):
        if record.decision.get("tool") == "perform_action":
            last_action_step = record
            break

    success_checkpoint = Checkpoint(
        check_type="text_visible",
        value="Application Details",
        description="Application detail page is loaded with all required data",
    )
    if last_action_step:
        # Use the URL of the page where data was extracted
        success_checkpoint = Checkpoint(
            check_type="url_contains",
            value="application/",
            description="Should be on application detail page",
        )

    artifact = CapabilityArtifact(
        artifact_id=str(uuid.uuid4()),
        name=name,
        description=description,
        target_url=target_url,
        target_app="LoanPro",
        inputs=inputs,
        outputs=outputs,
        steps=steps,
        success_checkpoint=success_checkpoint,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by="discovery",
        tags=["loan", "status", "read-only"],
    )

    return artifact


if __name__ == "__main__":
    print("Emitter OK — requires a DiscoveryResult to run")
