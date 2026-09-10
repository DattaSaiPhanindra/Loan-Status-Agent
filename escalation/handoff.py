"""Human-in-the-loop escalation and session handoff."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from playwright.async_api import Page

logger = logging.getLogger(__name__)


class SessionOwner(str, Enum):
    """Who currently controls the browser session."""

    AUTOMATION = "automation"
    HUMAN = "human"
    PAUSED = "paused"


class EscalationReason(str, Enum):
    """Why the system escalated to a human."""

    SESSION_EXPIRED = "session_expired"
    STUCK = "stuck"
    RISKY_ACTION = "risky_action"
    UNKNOWN_STATE = "unknown_state"
    REPLAY_FAILURE = "replay_failure"


@dataclass
class EscalationRequest:
    """Context passed to the human operator."""

    reason: EscalationReason
    message: str
    current_url: str = ""
    current_step: int = -1
    artifact_name: str = ""
    screenshot_path: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "reason": self.reason.value,
            "message": self.message,
            "current_url": self.current_url,
            "current_step": self.current_step,
            "artifact_name": self.artifact_name,
            "screenshot_path": self.screenshot_path,
            "timestamp": self.timestamp,
        }


@dataclass
class HandoffRecord:
    """Record of what happened during human control."""

    escalation: EscalationRequest
    started_at: float = 0.0
    ended_at: float = 0.0
    human_actions: list[str] = field(default_factory=list)
    resume_url: str = ""
    resolution: str = ""  # "resolved", "aborted", "partial"

    def to_dict(self) -> dict:
        return {
            "escalation": self.escalation.to_dict(),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": round(self.ended_at - self.started_at, 2)
            if self.ended_at
            else 0,
            "human_actions": self.human_actions,
            "resume_url": self.resume_url,
            "resolution": self.resolution,
        }


class SessionController:
    """Manages control transfer between automation and human operator.

    The key invariant: the same browser context and page are used throughout.
    Automation pauses, human takes over the same session, then automation resumes.
    """

    def __init__(self, page: Page):
        self._page = page
        self._owner = SessionOwner.AUTOMATION
        self._handoff_history: list[HandoffRecord] = []

    @property
    def owner(self) -> SessionOwner:
        return self._owner

    @property
    def page(self) -> Page:
        return self._page

    @property
    def history(self) -> list[HandoffRecord]:
        return self._handoff_history

    def detect_escalation_needed(
        self, page_text: str, url: str, step_error: str = ""
    ) -> EscalationRequest | None:
        """Detect if the current state requires human intervention."""
        text_lower = page_text.lower()

        if any(
            phrase in text_lower
            for phrase in [
                "session expired",
                "please log in again",
                "login again",
                "session timeout",
            ]
        ):
            return EscalationRequest(
                reason=EscalationReason.SESSION_EXPIRED,
                message="Session expired. Human needs to log in again on the same browser session.",
                current_url=url,
            )

        if any(
            phrase in text_lower
            for phrase in ["access denied", "forbidden", "not authorized"]
        ):
            return EscalationRequest(
                reason=EscalationReason.UNKNOWN_STATE,
                message="Access denied. Human may need to authenticate or request permissions.",
                current_url=url,
            )

        if step_error:
            return EscalationRequest(
                reason=EscalationReason.REPLAY_FAILURE,
                message=f"Replay failed: {step_error}. Human intervention needed.",
                current_url=url,
            )

        return None

    async def escalate(
        self,
        request: EscalationRequest,
        evidence_dir: Path | None = None,
    ) -> HandoffRecord:
        """Pause automation and hand control to a human operator.

        This is a BLOCKING call — it waits for the human to signal they're done.
        The human interacts with the same live browser page.
        """
        self._owner = SessionOwner.PAUSED
        record = HandoffRecord(escalation=request, started_at=time.time())

        if evidence_dir:
            try:
                ss = await self._page.screenshot(type="png")
                ss_path = evidence_dir / "escalation_before.png"
                ss_path.write_bytes(ss)
                request.screenshot_path = str(ss_path)
            except Exception:
                pass

        logger.warning(f"ESCALATION: {request.reason.value} — {request.message}")
        logger.info(f"Current URL: {request.current_url}")
        logger.info(f"Current step: {request.current_step}")

        self._owner = SessionOwner.HUMAN

        print("\n" + "=" * 60)
        print("HUMAN INTERVENTION REQUIRED")
        print("=" * 60)
        print(f"Reason: {request.reason.value}")
        print(f"Message: {request.message}")
        print(f"Current URL: {self._page.url}")
        print()
        print("The browser window is now under YOUR control.")
        print("Perform the necessary actions (e.g. log in again).")
        print("When done, come back here and press Enter to resume automation.")
        print("Type 'abort' to cancel the run.")
        print("=" * 60)

        loop = asyncio.get_event_loop()
        user_input = await loop.run_in_executor(
            None, input, "\nPress Enter when done (or type 'abort'): "
        )

        record.ended_at = time.time()
        record.resume_url = self._page.url
        record.human_actions.append(
            f"Human took control for {record.ended_at - record.started_at:.1f}s"
        )

        if user_input.strip().lower() == "abort":
            record.resolution = "aborted"
            self._owner = SessionOwner.PAUSED
            logger.info("Human aborted the run")
        else:
            record.resolution = "resolved"
            self._owner = SessionOwner.AUTOMATION
            logger.info(f"Control returned to automation. URL: {self._page.url}")

        if evidence_dir:
            try:
                ss = await self._page.screenshot(type="png")
                ss_path = evidence_dir / "escalation_after.png"
                ss_path.write_bytes(ss)
            except Exception:
                pass

        self._handoff_history.append(record)
        return record

    def save_history(self, path: Path) -> None:
        """Save handoff history to JSON."""
        data = [r.to_dict() for r in self._handoff_history]
        path.write_text(json.dumps(data, indent=2, default=str))


async def replay_with_escalation(
    page: Page,
    artifact,  # CapabilityArtifact
    params: dict,
    evidence_dir: Path,
    run_id: str,
) -> dict:
    """Run replay with escalation support.

    If replay hits session_expired, escalate to human, then retry from the failed step.
    Returns the final ReplayOutcome as a dict.
    """
    from replay.engine import run_replay

    controller = SessionController(page)
    run_dir = evidence_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    result = await run_replay(page, artifact, params, evidence_dir, run_id)

    if (
        result.status == "business_outcome" and result.outcome_type == "session_expired"
    ) or (result.status == "failure" and "session" in result.error_message.lower()):
        request = EscalationRequest(
            reason=EscalationReason.SESSION_EXPIRED,
            message="Session expired during replay. Please log in again.",
            current_url=page.url,
            current_step=result.failed_step
            if result.failed_step >= 0
            else len(result.steps),
            artifact_name=artifact.name,
        )

        record = await controller.escalate(request, run_dir)

        if record.resolution == "resolved":
            retry_run_id = f"{run_id}_retry"
            result = await run_replay(
                page, artifact, params, evidence_dir, retry_run_id
            )
            controller.save_history(run_dir / "escalation_log.json")

    elif result.status == "failure":
        try:
            body_text = await page.inner_text("body", timeout=3000)
            escalation = controller.detect_escalation_needed(
                body_text, page.url, result.error_message
            )
            if escalation:
                escalation.current_step = result.failed_step
                escalation.artifact_name = artifact.name

                record = await controller.escalate(escalation, run_dir)

                if record.resolution == "resolved":
                    retry_run_id = f"{run_id}_retry"
                    result = await run_replay(
                        page, artifact, params, evidence_dir, retry_run_id
                    )
                    controller.save_history(run_dir / "escalation_log.json")
        except Exception:
            pass

    return result


if __name__ == "__main__":
    req = EscalationRequest(
        reason=EscalationReason.SESSION_EXPIRED,
        message="Test escalation",
        current_url="http://test",
    )
    print(f"Escalation OK: {req.reason.value} — {req.message}")
    ctrl = SessionController(page=None)  # type: ignore[arg-type]
    print(f"Controller OK: owner={ctrl.owner.value}")
