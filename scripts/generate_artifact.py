"""Generate artifact from the successful discovery_b05e58b5 run."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from artifact.schema import (
    CapabilityArtifact,
    Checkpoint,
    InputParameter,
    LocatorStrategy,
    OutputField,
    StepAction,
    StepDefinition,
)
from artifact.store import save_artifact


def main():
    artifact = CapabilityArtifact(
        artifact_id=str(uuid.uuid4()),
        name="check_loan_application_status",
        description="Look up a loan application and extract its status, missing documents, and next steps",
        target_url="http://127.0.0.1:8080",
        target_app="LoanPro",
        inputs=[
            InputParameter(
                name="application_id",
                description="The loan application ID to look up",
                param_type="string",
                required=True,
                example="LN-1042",
            ),
        ],
        outputs=[
            OutputField(
                name="status",
                description="Current application status",
                field_type="string",
            ),
            OutputField(
                name="missing_documents",
                description="List of missing documents",
                field_type="list",
            ),
            OutputField(
                name="next_steps",
                description="Next action required",
                field_type="string",
            ),
        ],
        steps=[
            StepDefinition(
                step_index=0,
                description="Enter username",
                action=StepAction(
                    action_type="type",
                    locator=LocatorStrategy(
                        primary='input[name="username"]',
                        fallbacks=['textarea[name="username"]'],
                        role="textbox",
                        name="username",
                        description="Login username field",
                    ),
                    value="agent",
                ),
                risk_level="safe",
            ),
            StepDefinition(
                step_index=1,
                description="Enter password",
                action=StepAction(
                    action_type="type",
                    locator=LocatorStrategy(
                        primary='input[name="password"]',
                        fallbacks=['textarea[name="password"]'],
                        role="textbox",
                        name="password",
                        description="Login password field",
                    ),
                    value="agent",
                ),
                risk_level="safe",
            ),
            StepDefinition(
                step_index=2,
                description="Click Sign In button",
                action=StepAction(
                    action_type="click",
                    locator=LocatorStrategy(
                        primary='input[type="submit"][value="Sign In"]',
                        fallbacks=['button:has-text("Sign In")'],
                        role="button",
                        name="Sign In",
                        description="Login submit button",
                    ),
                ),
                checkpoint=Checkpoint(
                    check_type="url_contains",
                    value="search",
                    description="Should navigate to search page after login",
                ),
                risk_level="read",
            ),
            StepDefinition(
                step_index=3,
                description="Type application ID into search box",
                action=StepAction(
                    action_type="type",
                    locator=LocatorStrategy(
                        primary='input[name="q"]',
                        fallbacks=['textarea[name="q"]'],
                        role="textbox",
                        name="q",
                        description="Search input field",
                    ),
                    value="",
                    is_parameterized=True,
                    param_key="application_id",
                ),
                risk_level="read",
            ),
            StepDefinition(
                step_index=4,
                description="Click Search button",
                action=StepAction(
                    action_type="click",
                    locator=LocatorStrategy(
                        primary='input[type="submit"][value="Search"]',
                        fallbacks=['button:has-text("Search")'],
                        role="button",
                        name="Search",
                        description="Search submit button",
                    ),
                ),
                checkpoint=Checkpoint(
                    check_type="text_visible",
                    value="Application ID",
                    description="Search results table should appear",
                ),
                risk_level="read",
            ),
            StepDefinition(
                step_index=5,
                description="Click on application link in results",
                action=StepAction(
                    action_type="click",
                    locator=LocatorStrategy(
                        primary='a:has-text("__APP_ID__")',
                        fallbacks=[],
                        role="link",
                        name="__APP_ID__",
                        description="Application ID link in search results",
                    ),
                    value="",
                    is_parameterized=True,
                    param_key="application_id",
                ),
                checkpoint=Checkpoint(
                    check_type="url_contains",
                    value="application/",
                    description="Should navigate to application detail page",
                ),
                risk_level="read",
            ),
        ],
        success_checkpoint=Checkpoint(
            check_type="url_contains",
            value="application/",
            description="Should be on application detail page",
        ),
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by="discovery",
        tags=["loan", "status", "read-only"],
    )

    path = save_artifact(artifact, Path("artifacts"))
    print(f"Artifact saved: {path}")
    print(f"ID: {artifact.artifact_id}")
    print(f"Steps: {len(artifact.steps)}")
    print(f"Inputs: {[i.name for i in artifact.inputs]}")
    print(f"Outputs: {[o.name for o in artifact.outputs]}")
    print("\nArtifact JSON:")
    print(artifact.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
