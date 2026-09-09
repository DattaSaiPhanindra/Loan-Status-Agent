import click


@click.group()
def main():
    pass


@main.command()
@click.argument("goal")
@click.argument("url")
def discover(goal, url):
    click.echo("Not implemented")


@main.command()
@click.argument("artifact_path")
@click.argument("params")
def replay(artifact_path, params):
    click.echo("Not implemented")


@main.command("escalate-demo")
def escalate_demo():
    click.echo("Not implemented")


@main.command("serve-portal")
def serve_portal():
    click.echo("Not implemented")
