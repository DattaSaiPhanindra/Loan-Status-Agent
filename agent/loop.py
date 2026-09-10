"""Observe-decide-act discovery loop."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from agent.llm import LLMClient
from agent.surface import Action, Observation, PlaywrightSurface
from policy.guardrails import check_action_allowed, check_url_allowed, classify_risk

logger = logging.getLogger(__name__)


class StepRecord:
    """Record of a single step during discovery."""

    def __init__(
        self,
        step_index: int,
        observation: Observation,
        decision: dict,
        action_result: dict | None = None,
    ):
        self.step_index = step_index
        self.timestamp = time.time()
        self.observation = observation
        self.decision = decision
        self.action_result = action_result
        self.screenshot_path: str = ""

    def to_dict(self) -> dict:
        return {
            "step_index": self.step_index,
            "timestamp": self.timestamp,
            "url": self.observation.url,
            "elements_count": len(self.observation.elements),
            "decision": self.decision,
            "action_result": self.action_result,
            "screenshot": self.screenshot_path,
        }


class DiscoveryResult:
    """Result of a discovery run."""

    def __init__(self):
        self.success: bool = False
        self.goal: str = ""
        self.steps: list[StepRecord] = []
        self.extracted_data: dict = {}
        self.error: str = ""
        self.start_time: float = 0
        self.end_time: float = 0
        self.run_id: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "success": self.success,
            "goal": self.goal,
            "total_steps": len(self.steps),
            "extracted_data": self.extracted_data,
            "error": self.error,
            "duration_seconds": round(self.end_time - self.start_time, 2),
            "steps": [s.to_dict() for s in self.steps],
        }


async def run_discovery(
    surface: PlaywrightSurface,
    goal: str,
    run_id: str,
    evidence_dir: Path,
    max_steps: int = 25,
) -> DiscoveryResult:
    """Run LLM-driven discovery loop: observe -> decide -> act until goal met or stuck."""
    llm = LLMClient()
    result = DiscoveryResult()
    result.goal = goal
    result.run_id = run_id
    result.start_time = time.time()

    run_dir = evidence_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Discovery started: goal={goal!r}, run_id={run_id}")

    for step_num in range(max_steps):
        logger.info(f"Step {step_num}: observing...")

        observation = await surface.observe()

        try:
            screenshot = await surface.screenshot()
            screenshot_path = run_dir / f"step_{step_num:03d}.png"
            screenshot_path.write_bytes(screenshot)
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            screenshot_path = None

        try:
            decision = llm.decide(goal, observation, step_num)
        except Exception as e:
            logger.error(f"LLM error: {e}")
            result.error = f"LLM error at step {step_num}: {str(e)}"
            break

        logger.info(
            f"Step {step_num}: decision={decision['tool']} "
            f"args={json.dumps(decision.get('args', {}))[:200]}"
        )

        record = StepRecord(step_num, observation, decision)
        if screenshot_path:
            record.screenshot_path = str(screenshot_path)

        if decision["tool"] == "mark_complete":
            result.success = True
            result.extracted_data = decision["args"].get("extracted_data", {})
            record.action_result = {
                "status": "complete",
                "data": result.extracted_data,
            }
            result.steps.append(record)
            logger.info(
                f"Goal achieved at step {step_num}: "
                f"{decision['args'].get('summary', '')}"
            )
            break

        if decision["tool"] == "mark_stuck":
            result.error = decision["args"].get("reason", "Agent reported stuck")
            record.action_result = {"status": "stuck", "reason": result.error}
            result.steps.append(record)
            logger.warning(f"Agent stuck at step {step_num}: {result.error}")
            break

        if decision["tool"] == "perform_action":
            args = decision["args"]
            action = Action(
                action_type=args["action_type"],
                ref=args.get("ref", ""),
                value=args.get("value", ""),
                description=args.get("description", ""),
            )

            url_ok, url_reason = check_url_allowed(observation.url)
            if not url_ok:
                result.error = f"Policy violation at step {step_num}: {url_reason}"
                record.action_result = {"status": "blocked", "reason": url_reason}
                result.steps.append(record)
                logger.error(f"Policy blocked: {url_reason}")
                break

            risk = classify_risk(action.action_type, element_name=action.description)
            action_ok, action_reason = check_action_allowed(action.action_type, risk)
            if not action_ok:
                logger.warning(f"Action blocked by policy: {action_reason}")
                record.action_result = {"status": "blocked", "reason": action_reason}
                result.steps.append(record)
                continue

            action_result = await surface.act(action)
            record.action_result = {
                "status": "success" if action_result.success else "failed",
                "message": action_result.message,
                "error": action_result.error,
            }
            result.steps.append(record)

            if not action_result.success:
                logger.warning(f"Action failed: {action_result.error}")

            await asyncio.sleep(0.5)
        else:
            result.error = f"Unknown tool: {decision['tool']}"
            break
    else:
        result.error = f"Max steps ({max_steps}) reached without completing goal"

    result.end_time = time.time()

    log_path = run_dir / "run_log.json"
    log_path.write_text(json.dumps(result.to_dict(), indent=2, default=str))

    logger.info(
        f"Discovery finished: success={result.success}, steps={len(result.steps)}, "
        f"duration={result.end_time - result.start_time:.1f}s"
    )

    return result
