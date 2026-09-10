"""Three-way result contract for replay execution."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class StepResult(BaseModel):
    """Result of executing a single replay step."""

    step_index: int
    description: str
    status: Literal["success", "failed", "skipped"]
    message: str = ""
    error: str = ""
    screenshot_path: str = ""
    duration_seconds: float = 0.0


class ReplayOutcome(BaseModel):
    """Three-way result: success, business_outcome, or failure.

    This is the critical distinction the brief demands:
    - success: goal achieved, outputs extracted
    - business_outcome: a legitimate result that isn't an error (e.g. "application not found")
    - failure: something broke — needs debugging
    """

    status: Literal["success", "business_outcome", "failure"]

    # For success
    outputs: dict = {}

    # For business_outcome
    outcome_type: str = ""
    outcome_message: str = ""

    # For failure
    error_type: str = ""
    error_message: str = ""
    failed_step: int = -1
    expected: str = ""
    observed: str = ""

    # Always present
    steps: list[StepResult] = []
    total_duration_seconds: float = 0.0
    screenshot_path: str = ""
