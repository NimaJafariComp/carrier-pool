"""Command-line entry point for Carrier Pool backend operations."""

import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the backend version."""
    typer.echo("carrier-pool 0.1.0")


def main() -> None:
    """Run the Carrier Pool command-line interface."""
    app()
