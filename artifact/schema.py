"""Typed, versioned capability artifact models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LocatorStrategy(BaseModel):
    """Ranked strategies to locate a UI element during replay."""

    primary: str
    fallbacks: list[str] = []
    role: str = ""
    name: str = ""
    description: str = ""


class StepAction(BaseModel):
    """A single action within a step."""

    action_type: Literal["click", "type", "select", "navigate", "wait", "scroll"]
    locator: LocatorStrategy | None = None
    value: str = ""
    is_parameterized: bool = False
    param_key: str = ""


class Checkpoint(BaseModel):
    """Verification that a step reached the expected state."""

    check_type: Literal["url_contains", "element_visible", "text_visible", "url_equals"]
    value: str
    description: str = ""


class StepDefinition(BaseModel):
    """One step in a recorded workflow."""

    step_index: int
    description: str
    action: StepAction
    checkpoint: Checkpoint | None = None
    expected_url_pattern: str = ""
    is_critical: bool = True
    risk_level: Literal["safe", "read", "write", "irreversible"] = "read"


class InputParameter(BaseModel):
    """A typed input parameter the caller supplies per invocation."""

    name: str
    description: str
    param_type: Literal["string", "number", "boolean"] = "string"
    required: bool = True
    default: str = ""
    example: str = ""
    sensitive: bool = False


class OutputField(BaseModel):
    """A typed output extracted from the target application."""

    name: str
    description: str
    field_type: Literal["string", "number", "boolean", "list"] = "string"
    locator: LocatorStrategy | None = None
    extract_pattern: str = ""


class CapabilityArtifact(BaseModel):
    """A reusable, versioned automation capability."""

    artifact_id: str
    name: str
    version: str = "1.0.0"
    description: str

    target_url: str
    target_app: str = ""

    inputs: list[InputParameter]
    outputs: list[OutputField]

    steps: list[StepDefinition]

    success_checkpoint: Checkpoint

    created_at: str
    created_by: str = "discovery"
    tenant_id: str = ""
    base_artifact_id: str = ""
    tags: list[str] = []

    status: Literal["draft", "approved", "deprecated"] = "draft"
    replay_count: int = 0
    last_replayed_at: str = ""

    def get_parameterized_steps(self) -> list[StepDefinition]:
        """Return steps that use input parameters."""
        return [s for s in self.steps if s.action.is_parameterized]


if __name__ == "__main__":
    from datetime import datetime, timezone
    import uuid

    artifact = CapabilityArtifact(
        artifact_id=str(uuid.uuid4()),
        name="check_loan_application_status",
        description="Look up a loan application and extract its status, missing documents, and next steps",
        target_url="http://127.0.0.1:8080",
        target_app="LoanPro",
        inputs=[
            InputParameter(
                name="application_id",
                description="Loan application ID",
                example="LN-1042",
            )
        ],
        outputs=[
            OutputField(name="status", description="Current application status"),
            OutputField(
                name="missing_docs",
                description="List of missing documents",
                field_type="list",
            ),
            OutputField(name="next_step", description="Next action required"),
        ],
        steps=[],
        success_checkpoint=Checkpoint(
            check_type="text_visible",
            value="Application Details",
            description="Detail page loaded",
        ),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    print(f"Artifact OK: {artifact.name} v{artifact.version}")
    print(f"  Inputs: {[i.name for i in artifact.inputs]}")
    print(f"  Outputs: {[o.name for o in artifact.outputs]}")

    json_str = artifact.model_dump_json(indent=2)
    loaded = CapabilityArtifact.model_validate_json(json_str)
    assert loaded.artifact_id == artifact.artifact_id
    print("  Round-trip OK")
