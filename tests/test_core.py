"""Core tests for schema, replay result, policy, and store."""

import uuid
from datetime import datetime, timezone

import pytest

from artifact.schema import (
    CapabilityArtifact,
    Checkpoint,
    InputParameter,
    LocatorStrategy,
    OutputField,
    StepAction,
    StepDefinition,
)
from artifact.store import list_artifacts, load_artifact, save_artifact
from policy.guardrails import (
    check_action_allowed,
    check_url_allowed,
    classify_risk,
    redact_dict,
    redact_text,
)
from replay.result import ReplayOutcome, StepResult


# --- Artifact Schema ---


def _make_artifact(**overrides) -> CapabilityArtifact:
    defaults = dict(
        artifact_id=str(uuid.uuid4()),
        name="test_capability",
        description="Test",
        target_url="http://127.0.0.1:8080",
        inputs=[InputParameter(name="app_id", description="ID")],
        outputs=[OutputField(name="status", description="Status")],
        steps=[
            StepDefinition(
                step_index=0,
                description="Click button",
                action=StepAction(
                    action_type="click",
                    locator=LocatorStrategy(primary="button#submit"),
                ),
            )
        ],
        success_checkpoint=Checkpoint(check_type="url_contains", value="/done"),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    defaults.update(overrides)
    return CapabilityArtifact(**defaults)


class TestArtifactSchema:
    def test_round_trip_json(self):
        artifact = _make_artifact()
        json_str = artifact.model_dump_json()
        loaded = CapabilityArtifact.model_validate_json(json_str)
        assert loaded.artifact_id == artifact.artifact_id
        assert loaded.name == artifact.name
        assert len(loaded.steps) == 1
        assert len(loaded.inputs) == 1
        assert len(loaded.outputs) == 1

    def test_parameterized_steps(self):
        artifact = _make_artifact(
            steps=[
                StepDefinition(
                    step_index=0,
                    description="Type ID",
                    action=StepAction(
                        action_type="type",
                        locator=LocatorStrategy(primary="input[name=q]"),
                        is_parameterized=True,
                        param_key="app_id",
                    ),
                ),
                StepDefinition(
                    step_index=1,
                    description="Click",
                    action=StepAction(
                        action_type="click",
                        locator=LocatorStrategy(primary="button"),
                    ),
                ),
            ]
        )
        parameterized = artifact.get_parameterized_steps()
        assert len(parameterized) == 1
        assert parameterized[0].action.param_key == "app_id"

    def test_version_default(self):
        artifact = _make_artifact()
        assert artifact.version == "1.0.0"
        assert artifact.status == "draft"

    def test_risk_levels(self):
        for level in ("safe", "read", "write", "irreversible"):
            step = StepDefinition(
                step_index=0,
                description="test",
                action=StepAction(
                    action_type="click", locator=LocatorStrategy(primary="x")
                ),
                risk_level=level,
            )
            assert step.risk_level == level


# --- Artifact Store ---


class TestArtifactStore:
    def test_save_and_load(self, tmp_path):
        artifact = _make_artifact()
        path = save_artifact(artifact, tmp_path)
        assert path.exists()
        loaded = load_artifact(path)
        assert loaded.artifact_id == artifact.artifact_id

    def test_list_artifacts(self, tmp_path):
        a1 = _make_artifact(name="first")
        a2 = _make_artifact(name="second")
        save_artifact(a1, tmp_path)
        save_artifact(a2, tmp_path)
        items = list_artifacts(tmp_path)
        assert len(items) == 2

    def test_list_empty_dir(self, tmp_path):
        assert list_artifacts(tmp_path) == []

    def test_list_nonexistent_dir(self, tmp_path):
        assert list_artifacts(tmp_path / "nope") == []


# --- Policy ---


class TestPolicy:
    def test_allowed_url(self):
        ok, _ = check_url_allowed("http://127.0.0.1:8080/search")
        assert ok

    def test_blocked_admin_url(self):
        ok, reason = check_url_allowed(
            "http://127.0.0.1:8080/admin/expire-session"
        )
        assert not ok
        assert "blocked" in reason.lower()

    def test_external_url_blocked(self):
        ok, _ = check_url_allowed("http://evil.com/steal")
        assert not ok

    def test_action_allowed(self):
        ok, _ = check_action_allowed("click", "read")
        assert ok

    def test_write_action_blocked(self):
        ok, reason = check_action_allowed("click", "write")
        assert not ok
        assert "confirmation" in reason.lower()

    def test_risk_safe(self):
        assert classify_risk("navigate") == "safe"
        assert classify_risk("wait") == "safe"

    def test_risk_read(self):
        assert classify_risk("click", "Search") == "read"

    def test_risk_write(self):
        assert classify_risk("click", "Submit Application") == "write"

    def test_risk_irreversible(self):
        assert classify_risk("click", "Delete Account") == "irreversible"

    def test_redact_ssn(self):
        assert "[REDACTED_SSN]" in redact_text("SSN is 123-45-6789")

    def test_redact_dict_password(self):
        result = redact_dict({"password": "secret123", "name": "test"})
        assert result["password"] == "[REDACTED]"
        assert result["name"] == "test"


# --- Replay Result ---


class TestReplayResult:
    def test_success_outcome(self):
        result = ReplayOutcome(
            status="success",
            outputs={"status": "Active"},
            steps=[StepResult(step_index=0, description="test", status="success")],
        )
        assert result.status == "success"
        assert result.outputs["status"] == "Active"

    def test_business_outcome(self):
        result = ReplayOutcome(
            status="business_outcome",
            outcome_type="not_found",
            outcome_message="Application not found",
        )
        assert result.status == "business_outcome"
        assert result.outcome_type == "not_found"

    def test_failure_outcome(self):
        result = ReplayOutcome(
            status="failure",
            error_type="locator_failed",
            error_message="Button not found",
            failed_step=3,
            expected="Submit button visible",
            observed="No matching element",
        )
        assert result.status == "failure"
        assert result.failed_step == 3
        assert result.expected != result.observed

    def test_three_statuses_are_distinct(self):
        statuses = {"success", "business_outcome", "failure"}
        assert len(statuses) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
