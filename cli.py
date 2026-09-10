"""CLI entry point for loan-status-agent."""

import asyncio
import json
import logging
import uuid

import click

from config import Settings


@click.group()
def main():
    """Loan Status Agent — discover, replay, and manage automation capabilities."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@main.command()
@click.argument("goal")
@click.argument("url")
@click.option("--max-steps", default=25, help="Maximum agent steps")
@click.option("--headless", is_flag=True, default=False, help="Run browser headless")
def discover(goal: str, url: str, max_steps: int, headless: bool):
    """Run LLM-driven discovery against the portal."""
    asyncio.run(_discover(goal, url, max_steps, headless))


async def _discover(goal: str, url: str, max_steps: int, headless: bool):
    from playwright.async_api import async_playwright

    from agent.loop import run_discovery
    from agent.surface import PlaywrightSurface
    from artifact.emitter import emit_artifact
    from artifact.store import save_artifact

    settings = Settings()
    run_id = f"discovery_{uuid.uuid4().hex[:8]}"

    click.echo(f"Starting discovery run: {run_id}")
    click.echo(f"Goal: {goal}")
    click.echo(f"Target: {url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page()
        await page.goto(url, timeout=15000)

        surface = PlaywrightSurface(page)
        result = await run_discovery(
            surface=surface,
            goal=goal,
            run_id=run_id,
            evidence_dir=settings.evidence_dir,
            max_steps=max_steps,
        )

        await browser.close()

    if result.success:
        # Emit and save artifact
        base_url = url.rsplit("/", 1)[0] if "/" in url else url
        artifact = emit_artifact(
            result=result,
            name="check_loan_application_status",
            description="Look up a loan application and extract its status, missing documents, and next steps",
            target_url=base_url,
        )
        artifact_path = save_artifact(artifact, settings.artifacts_dir)

        click.echo(f"\n{'='*60}")
        click.echo(f"SUCCESS - Goal achieved in {len(result.steps)} steps")
        click.echo("Extracted data:")
        click.echo(json.dumps(result.extracted_data, indent=2))
        click.echo(f"Artifact saved to: {artifact_path}")
        click.echo(f"Artifact ID: {artifact.artifact_id}")
        click.echo(f"Evidence saved to: {settings.evidence_dir / run_id}")
        click.echo(f"{'='*60}")
    else:
        click.echo(f"\n{'='*60}")
        click.echo(f"FAILED - {result.error}")
        click.echo(f"Steps taken: {len(result.steps)}")
        click.echo(f"Evidence saved to: {settings.evidence_dir / run_id}")
        click.echo(f"{'='*60}")


@main.command()
@click.argument("artifact_path")
@click.argument("params")
@click.option("--headless", is_flag=True, default=False, help="Run browser headless")
def replay(artifact_path: str, params: str, headless: bool):
    """Replay a saved artifact deterministically."""
    asyncio.run(_replay(artifact_path, params, headless))


async def _replay(artifact_path: str, params_json: str, headless: bool):
    from pathlib import Path

    from playwright.async_api import async_playwright

    from artifact.store import load_artifact
    from replay.engine import run_replay

    settings = Settings()
    run_id = f"replay_{uuid.uuid4().hex[:8]}"

    artifact = load_artifact(Path(artifact_path))
    click.echo(f"Loaded artifact: {artifact.name} v{artifact.version}")
    click.echo(f"Artifact ID: {artifact.artifact_id}")

    try:
        params = json.loads(params_json)
    except json.JSONDecodeError:
        click.echo(f"ERROR: Invalid JSON params: {params_json}")
        return

    click.echo(f"Replay run: {run_id}")
    click.echo(f"Params: {json.dumps(params)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page()

        result = await run_replay(
            page=page,
            artifact=artifact,
            params=params,
            evidence_dir=settings.evidence_dir,
            run_id=run_id,
        )

        await browser.close()

    click.echo(f"\n{'='*60}")
    if result.status == "success":
        click.echo(
            f"SUCCESS - Replay completed in {result.total_duration_seconds:.1f}s"
        )
        click.echo("Outputs:")
        click.echo(json.dumps(result.outputs, indent=2))
    elif result.status == "business_outcome":
        click.echo(f"BUSINESS OUTCOME - {result.outcome_type}")
        click.echo(f"Message: {result.outcome_message}")
    else:
        click.echo(f"FAILURE - {result.error_type}")
        click.echo(f"Error: {result.error_message}")
        click.echo(f"Failed at step: {result.failed_step}")
        if result.expected:
            click.echo(f"Expected: {result.expected}")
        if result.observed:
            click.echo(f"Observed: {result.observed}")

    click.echo(f"Steps: {len(result.steps)}")
    click.echo(f"Evidence: {settings.evidence_dir / run_id}")
    click.echo(f"{'='*60}")


@main.command("escalate-demo")
@click.option("--port", default=8080)
def escalate_demo(port: int):
    """Demo human handoff on session expiry."""
    asyncio.run(_escalate_demo(port))


async def _escalate_demo(port: int):
    import glob
    from pathlib import Path

    import httpx
    from playwright.async_api import async_playwright

    from artifact.store import load_artifact
    from escalation.handoff import EscalationReason, EscalationRequest, SessionController
    from replay.engine import run_replay

    settings = Settings()
    run_id = f"escalation_{uuid.uuid4().hex[:8]}"

    files = glob.glob("artifacts/*.json")
    if not files:
        click.echo("No artifacts found. Run discovery first.")
        return
    artifact = load_artifact(Path(files[0]))

    click.echo(f"Escalation demo: {run_id}")
    click.echo(f"Artifact: {artifact.name}")
    click.echo("This will: replay with LN-1042, force session expiry mid-run,")
    click.echo("pause for human login, then resume.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        # Step 1: Set short TTL to force expiry
        async with httpx.AsyncClient() as client:
            await client.post(
                f"http://127.0.0.1:{port}/admin/set-ttl",
                data={"ttl": "2"},
                follow_redirects=True,
            )
        click.echo("Set session TTL to 2 seconds.\n")

        # Step 2: Run replay — it will log in, but session expires mid-flow
        params = {"application_id": "LN-1042"}
        result = await run_replay(
            page=page,
            artifact=artifact,
            params=params,
            evidence_dir=settings.evidence_dir,
            run_id=run_id,
        )

        click.echo(f"\nFirst attempt result: {result.status}")

        if result.status != "success":
            # Step 3: Reset TTL back to normal BEFORE human logs in
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"http://127.0.0.1:{port}/admin/set-ttl",
                    data={"ttl": "300"},
                    follow_redirects=True,
                )
            click.echo("TTL reset to 300 seconds for retry.\n")

            # Step 4: Escalate to human
            controller = SessionController(page)
            request = EscalationRequest(
                reason=EscalationReason.SESSION_EXPIRED,
                message="Session expired during replay. Please log in again in the browser window.",
                current_url=page.url,
                current_step=result.failed_step
                if hasattr(result, "failed_step")
                else -1,
                artifact_name=artifact.name,
            )

            run_dir = settings.evidence_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            record = await controller.escalate(request, run_dir)

            if record.resolution == "resolved":
                # Step 5: Retry replay with normal TTL
                retry_id = f"{run_id}_retry"
                result = await run_replay(
                    page=page,
                    artifact=artifact,
                    params=params,
                    evidence_dir=settings.evidence_dir,
                    run_id=retry_id,
                )
                controller.save_history(run_dir / "escalation_log.json")
            else:
                click.echo("Human aborted.")

        await browser.close()

    click.echo(f"\n{'='*60}")
    if result.status == "success":
        click.echo("SUCCESS after escalation! Outputs:")
        click.echo(json.dumps(result.outputs, indent=2))
    elif result.status == "business_outcome":
        click.echo(f"BUSINESS OUTCOME: {result.outcome_type}")
        click.echo(f"Message: {result.outcome_message}")
    else:
        click.echo(f"FAILURE: {result.error_message}")
    click.echo(f"Evidence: {settings.evidence_dir / run_id}")
    click.echo(f"{'='*60}")


@main.command("serve-portal")
@click.option("--port", default=8080)
def serve_portal(port: int):
    """Start the legacy lending portal."""
    import uvicorn

    settings = Settings()
    uvicorn.run("portal.app:app", host=settings.portal_host, port=port, reload=False)


if __name__ == "__main__":
    main()
