"""Deterministic replay engine — executes artifacts without LLM."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from artifact.schema import CapabilityArtifact, Checkpoint, StepDefinition
from policy.guardrails import check_url_allowed
from replay.result import ReplayOutcome, StepResult

logger = logging.getLogger(__name__)

# Business outcome patterns — these are legitimate results, NOT errors
BUSINESS_OUTCOMES = {
    "not_found": [
        "not found",
        "no applications found",
        "no results",
        "does not exist",
        "no matching",
        "invalid application",
    ],
    "access_denied": [
        "access denied",
        "unauthorized",
        "permission denied",
        "not authorized",
        "forbidden",
    ],
    "validation_error": [
        "invalid input",
        "validation error",
        "required field",
        "please enter",
        "must be",
    ],
    "session_expired": [
        "session expired",
        "please log in again",
        "login again",
        "session timeout",
    ],
}


def detect_business_outcome(page_text: str) -> tuple[str, str] | None:
    """Check if current page shows a business outcome (not an error).
    Returns (outcome_type, message) or None.
    """
    text_lower = page_text.lower()
    for outcome_type, patterns in BUSINESS_OUTCOMES.items():
        for pattern in patterns:
            if pattern in text_lower:
                return outcome_type, f"Business outcome detected: {pattern}"
    return None


async def verify_checkpoint(page: Page, checkpoint: Checkpoint) -> tuple[bool, str]:
    """Verify a checkpoint condition. Returns (passed, detail)."""
    try:
        if checkpoint.check_type == "url_contains":
            current_url = page.url
            passed = checkpoint.value in current_url
            return (
                passed,
                f"URL '{current_url}' {'contains' if passed else 'does not contain'} '{checkpoint.value}'",
            )

        if checkpoint.check_type == "url_equals":
            current_url = page.url
            passed = current_url == checkpoint.value
            return passed, f"URL {'matches' if passed else 'does not match'} expected"

        if checkpoint.check_type == "text_visible":
            try:
                body_text = await page.inner_text("body", timeout=3000)
                passed = checkpoint.value.lower() in body_text.lower()
                return (
                    passed,
                    f"Text '{checkpoint.value}' {'found' if passed else 'not found'} on page",
                )
            except Exception:
                return False, "Could not read page text"

        if checkpoint.check_type == "element_visible":
            try:
                locator = page.locator(checkpoint.value)
                visible = await locator.is_visible(timeout=3000)
                return (
                    visible,
                    f"Element '{checkpoint.value}' {'visible' if visible else 'not visible'}",
                )
            except Exception:
                return False, f"Element '{checkpoint.value}' not found"

    except Exception as e:
        return False, f"Checkpoint error: {str(e)}"

    return False, f"Unknown checkpoint type: {checkpoint.check_type}"


def resolve_parameterized_value(step: StepDefinition, params: dict) -> str:
    """Resolve a parameterized value from input params."""
    if step.action.is_parameterized and step.action.param_key:
        value = params.get(step.action.param_key, "")
        if not value:
            raise ValueError(f"Missing required parameter: {step.action.param_key}")
        return str(value)
    return step.action.value


def resolve_locator_text(text: str, params: dict) -> str:
    """Replace __APP_ID__ and similar placeholders in locator selectors."""
    result = text
    for key, value in params.items():
        result = result.replace("__APP_ID__", str(value))
        result = result.replace(f"__{key.upper()}__", str(value))
    return result


async def execute_step(page: Page, step: StepDefinition, params: dict) -> StepResult:
    """Execute a single replay step. Returns StepResult."""
    start = time.time()
    description = step.description

    try:
        value = resolve_parameterized_value(step, params)
        action = step.action

        if not action.locator and action.action_type not in ("navigate", "wait", "scroll"):
            return StepResult(
                step_index=step.step_index,
                description=description,
                status="failed",
                error=f"No locator for action type '{action.action_type}'",
                duration_seconds=time.time() - start,
            )

        locator_resolved = False
        last_error = ""

        if action.locator:
            selectors = [action.locator.primary] + action.locator.fallbacks
            selectors = [resolve_locator_text(s, params) for s in selectors]

            for selector in selectors:
                try:
                    loc = page.locator(selector)
                    count = await loc.count()
                    if count > 0:
                        if action.action_type == "click":
                            await loc.first.click(timeout=5000)
                        elif action.action_type == "type":
                            await loc.first.fill(value, timeout=5000)
                        elif action.action_type == "select":
                            await loc.first.select_option(value, timeout=5000)
                        locator_resolved = True
                        break
                except Exception as e:
                    last_error = f"{selector}: {str(e)}"
                    continue

            if not locator_resolved:
                return StepResult(
                    step_index=step.step_index,
                    description=description,
                    status="failed",
                    error=f"No locator matched. Last error: {last_error}",
                    duration_seconds=time.time() - start,
                )
        else:
            if action.action_type == "navigate":
                await page.goto(value, timeout=10000)
            elif action.action_type == "wait":
                await asyncio.sleep(min(float(value) if value else 1.0, 5.0))
            elif action.action_type == "scroll":
                await page.evaluate("window.scrollBy(0, 300)")

        await asyncio.sleep(0.5)

        if step.checkpoint:
            passed, detail = await verify_checkpoint(page, step.checkpoint)
            if not passed:
                return StepResult(
                    step_index=step.step_index,
                    description=description,
                    status="failed",
                    error=f"Checkpoint failed: {detail}",
                    duration_seconds=time.time() - start,
                )

        return StepResult(
            step_index=step.step_index,
            description=description,
            status="success",
            message=f"Completed: {description}",
            duration_seconds=time.time() - start,
        )

    except PlaywrightTimeout as e:
        return StepResult(
            step_index=step.step_index,
            description=description,
            status="failed",
            error=f"Timeout: {str(e)[:200]}",
            duration_seconds=time.time() - start,
        )
    except ValueError as e:
        return StepResult(
            step_index=step.step_index,
            description=description,
            status="failed",
            error=str(e),
            duration_seconds=time.time() - start,
        )
    except Exception as e:
        return StepResult(
            step_index=step.step_index,
            description=description,
            status="failed",
            error=f"{type(e).__name__}: {str(e)[:200]}",
            duration_seconds=time.time() - start,
        )


async def extract_outputs(page: Page, artifact: CapabilityArtifact) -> dict:
    """Extract declared outputs from the current page."""
    outputs = {}
    try:
        body_text = await page.inner_text("body", timeout=3000)
    except Exception:
        return outputs

    for output_field in artifact.outputs:
        name = output_field.name

        if output_field.locator and output_field.locator.primary:
            try:
                loc = page.locator(output_field.locator.primary)
                text = await loc.first.inner_text(timeout=3000)
                outputs[name] = text.strip()
                continue
            except Exception:
                pass

        if output_field.field_type == "list":
            outputs[name] = extract_list_from_text(body_text, name)
        else:
            outputs[name] = extract_value_from_text(body_text, name)

    return outputs


def extract_value_from_text(text: str, field_name: str) -> str:
    """Extract a named value from page text using heuristics."""
    lines = text.split("\n")
    field_variants = [
        field_name.replace("_", " "),
        field_name.replace("_", " ").title(),
        field_name,
    ]

    for line in lines:
        line_stripped = line.strip()
        for variant in field_variants:
            if variant.lower() in line_stripped.lower():
                parts = re.split(r"[:\t]", line_stripped, maxsplit=1)
                if len(parts) > 1:
                    return parts[1].strip()
                idx = lines.index(line)
                if idx + 1 < len(lines):
                    next_line = lines[idx + 1].strip()
                    if next_line and not any(
                        v.lower() in next_line.lower() for v in field_variants
                    ):
                        return next_line
    return ""


def extract_list_from_text(text: str, field_name: str) -> list[str]:
    """Extract a list of items from page text."""
    items = []
    lines = text.split("\n")
    in_section = False

    for line in lines:
        stripped = line.strip()
        if "MISSING" in stripped:
            parts = stripped.split("MISSING")
            doc_name = parts[0].strip().rstrip("-").rstrip(":").strip()
            if doc_name:
                items.append(doc_name)

    if items:
        return items

    field_label = field_name.replace("_", " ").lower()
    for i, line in enumerate(lines):
        if field_label in line.lower():
            in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if not stripped:
                continue
            if any(
                stripped.lower().startswith(label)
                for label in ["status", "next", "application", "loan", "date"]
            ):
                break
            items.append(stripped)

    return items


async def run_replay(
    page: Page,
    artifact: CapabilityArtifact,
    params: dict,
    evidence_dir: Path | None = None,
    run_id: str = "",
) -> ReplayOutcome:
    """Execute a full replay of an artifact. Returns a three-way ReplayOutcome."""
    start = time.time()
    step_results: list[StepResult] = []

    for input_param in artifact.inputs:
        if input_param.required and input_param.name not in params:
            return ReplayOutcome(
                status="failure",
                error_type="missing_parameter",
                error_message=f"Missing required parameter: {input_param.name}",
                steps=step_results,
                total_duration_seconds=time.time() - start,
            )

    run_dir = None
    if evidence_dir and run_id:
        run_dir = evidence_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

    try:
        target = artifact.target_url
        if not target.endswith("/login"):
            target = target.rstrip("/") + "/login"
        await page.goto(target, timeout=10000)
    except Exception as e:
        return ReplayOutcome(
            status="failure",
            error_type="navigation_failed",
            error_message=f"Could not navigate to {artifact.target_url}: {str(e)}",
            steps=step_results,
            total_duration_seconds=time.time() - start,
        )

    for step in artifact.steps:
        logger.info(f"Replay step {step.step_index}: {step.description}")

        url_ok, url_reason = check_url_allowed(page.url)
        if not url_ok:
            return ReplayOutcome(
                status="failure",
                error_type="policy_violation",
                error_message=f"URL not allowed: {url_reason}",
                failed_step=step.step_index,
                steps=step_results,
                total_duration_seconds=time.time() - start,
            )

        step_result = await execute_step(page, step, params)

        if run_dir:
            try:
                ss = await page.screenshot(type="png")
                ss_path = run_dir / f"step_{step.step_index:03d}.png"
                ss_path.write_bytes(ss)
                step_result.screenshot_path = str(ss_path)
            except Exception:
                pass

        step_results.append(step_result)

        if step_result.status == "failed":
            try:
                body_text = await page.inner_text("body", timeout=3000)
                outcome = detect_business_outcome(body_text)
                if outcome:
                    outcome_type, outcome_msg = outcome
                    final_ss = ""
                    if run_dir:
                        try:
                            ss = await page.screenshot(type="png")
                            ss_path = run_dir / "final.png"
                            ss_path.write_bytes(ss)
                            final_ss = str(ss_path)
                        except Exception:
                            pass

                    return ReplayOutcome(
                        status="business_outcome",
                        outcome_type=outcome_type,
                        outcome_message=outcome_msg,
                        steps=step_results,
                        total_duration_seconds=time.time() - start,
                        screenshot_path=final_ss,
                    )
            except Exception:
                pass

            if step.is_critical:
                final_ss = ""
                if run_dir:
                    try:
                        ss = await page.screenshot(type="png")
                        ss_path = run_dir / "failure.png"
                        ss_path.write_bytes(ss)
                        final_ss = str(ss_path)
                    except Exception:
                        pass

                return ReplayOutcome(
                    status="failure",
                    error_type="step_failed",
                    error_message=step_result.error,
                    failed_step=step.step_index,
                    expected=step.description,
                    observed=step_result.error,
                    steps=step_results,
                    total_duration_seconds=time.time() - start,
                    screenshot_path=final_ss,
                )
            else:
                logger.warning(f"Non-critical step {step.step_index} failed, continuing")

    if artifact.success_checkpoint:
        passed, detail = await verify_checkpoint(page, artifact.success_checkpoint)
        if not passed:
            try:
                body_text = await page.inner_text("body", timeout=3000)
                outcome = detect_business_outcome(body_text)
                if outcome:
                    return ReplayOutcome(
                        status="business_outcome",
                        outcome_type=outcome[0],
                        outcome_message=outcome[1],
                        steps=step_results,
                        total_duration_seconds=time.time() - start,
                    )
            except Exception:
                pass

            return ReplayOutcome(
                status="failure",
                error_type="checkpoint_failed",
                error_message=f"Success checkpoint failed: {detail}",
                expected=artifact.success_checkpoint.description,
                observed=detail,
                steps=step_results,
                total_duration_seconds=time.time() - start,
            )

    outputs = await extract_outputs(page, artifact)

    final_ss = ""
    if run_dir:
        try:
            ss = await page.screenshot(type="png")
            ss_path = run_dir / "final.png"
            ss_path.write_bytes(ss)
            final_ss = str(ss_path)
        except Exception:
            pass

    if run_dir:
        log = {
            "run_id": run_id,
            "artifact_id": artifact.artifact_id,
            "params": {
                k: "[REDACTED]"
                if artifact.inputs
                and any(i.sensitive and i.name == k for i in artifact.inputs)
                else v
                for k, v in params.items()
            },
            "status": "success",
            "outputs": outputs,
            "steps": [s.model_dump() for s in step_results],
            "total_duration_seconds": time.time() - start,
        }
        log_path = run_dir / "replay_log.json"
        log_path.write_text(json.dumps(log, indent=2, default=str))

    return ReplayOutcome(
        status="success",
        outputs=outputs,
        steps=step_results,
        total_duration_seconds=time.time() - start,
        screenshot_path=final_ss,
    )
