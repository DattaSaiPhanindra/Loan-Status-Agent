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
def replay(artifact_path: str, params: str):
    """Replay a saved artifact deterministically."""
    click.echo("Not implemented yet")


@main.command("escalate-demo")
def escalate_demo():
    """Demo human handoff on session expiry."""
    click.echo("Not implemented yet")


@main.command("serve-portal")
@click.option("--port", default=8080)
def serve_portal(port: int):
    """Start the legacy lending portal."""
    import uvicorn

    settings = Settings()
    uvicorn.run("portal.app:app", host=settings.portal_host, port=port, reload=False)


if __name__ == "__main__":
    main()
