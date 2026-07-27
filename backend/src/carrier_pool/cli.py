"""Command-line entry point for Carrier Pool backend operations."""

from pathlib import Path
from typing import Annotated

import typer

from carrier_pool.generator.manifest import write_scenarios_manifest
from carrier_pool.generator.scheduler import write_sync_files
from carrier_pool.generator.validator import validate_generated_data

app = typer.Typer(no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the backend version."""
    typer.echo("carrier-pool 0.1.0")


@app.command()
def generate(
    data_root: Annotated[Path, typer.Option("--data-root", file_okay=False)] = Path("data"),
) -> None:
    """Write deterministic generated sync JSON files."""
    paths = write_sync_files(data_root)
    manifest_path = write_scenarios_manifest(data_root)
    typer.echo(f"generated {len(paths)} sync files and {manifest_path}")


@app.command("validate-data")
def validate_data(
    data_root: Annotated[Path, typer.Option("--data-root", file_okay=False)] = Path("data"),
) -> None:
    """Validate generated sync JSON and scenario metadata without database writes."""
    report = validate_generated_data(data_root)
    typer.echo(f"validated {report.sync_file_count} sync files")


def main() -> None:
    """Run the Carrier Pool command-line interface."""
    app()
