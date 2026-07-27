"""Command-line entry point for Carrier Pool backend operations."""

import json
import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from carrier_pool.domain.types import SourceSystem
from carrier_pool.generator.manifest import write_scenarios_manifest
from carrier_pool.generator.scheduler import write_sync_files
from carrier_pool.generator.validator import validate_generated_data
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import (
    BrokerOSIngestionCoordinator,
    FreightFlowIngestionCoordinator,
    HaulDeskIngestionCoordinator,
    IngestionResult,
)
from carrier_pool.ingestion.discovery import (
    DiscoveredSync,
    FileIngestionOrchestrator,
    SourceBinding,
    discover_sync_file,
)
from carrier_pool.ingestion.rebuild import rebuild_current_projections
from carrier_pool.ingestion.reporting import ingestion_summary

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


@app.command("ingest-file")
def ingest_file(
    path: Path,
    tenant_id: Annotated[str, typer.Option("--tenant-id")],
    source_system: Annotated[SourceSystem, typer.Option("--source-system")],
) -> None:
    """Ingest one strict generated sync file with an explicit trusted binding."""
    binding = SourceBinding(path.parent, tenant_id, source_system)
    result = _ingest_discovered(discover_sync_file(path, binding))
    typer.echo(_result_message(result))


@app.command("ingest-all")
def ingest_all(
    freightflow_tenant_id: Annotated[str, typer.Option("--freightflow-tenant-id")],
    hauldesk_tenant_id: Annotated[str, typer.Option("--hauldesk-tenant-id")],
    brokeros_tenant_id: Annotated[str, typer.Option("--brokeros-tenant-id")],
    data_root: Annotated[Path, typer.Option("--data-root", file_okay=False)] = Path("data"),
) -> None:
    """Ingest all generated files globally in filename timestamp order."""
    bindings = (
        SourceBinding(
            data_root / "tms_a_freightflow", freightflow_tenant_id, SourceSystem.FREIGHTFLOW
        ),
        SourceBinding(data_root / "tms_b_hauldesk", hauldesk_tenant_id, SourceSystem.HAULDESK),
        SourceBinding(data_root / "tms_c_brokeros", brokeros_tenant_id, SourceSystem.BROKEROS),
    )
    results = FileIngestionOrchestrator(bindings).ingest_all(_ingest_discovered)
    for result in results:
        typer.echo(_result_message(result))
    typer.echo(f"ingested {len(results)} files")


@app.command("rebuild-projections")
def rebuild_projections(
    tenant_id: Annotated[str, typer.Option("--tenant-id")],
) -> None:
    """Rebuild one tenant's mutable current projections from immutable facts."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise typer.BadParameter("DATABASE_URL is required for rebuilding projections.")
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            result = rebuild_current_projections(session, UUID(tenant_id))
    finally:
        engine.dispose()
    typer.echo(f"rebuilt loads={result.loads_rebuilt}; stops={result.stops_rebuilt}")


@app.command("ingestion-summary")
def show_ingestion_summary(
    tenant_id: Annotated[str, typer.Option("--tenant-id")],
) -> None:
    """Print compact tenant-scoped ingestion totals for the review demo."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise typer.BadParameter("DATABASE_URL is required for ingestion summary.")
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            summary = ingestion_summary(session, UUID(tenant_id))
    finally:
        engine.dispose()
    typer.echo(json.dumps(summary, sort_keys=True))


def _ingest_discovered(sync: DiscoveredSync) -> IngestionResult:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise typer.BadParameter("DATABASE_URL is required for ingestion.")
    coordinator = {
        SourceSystem.FREIGHTFLOW: FreightFlowIngestionCoordinator(),
        SourceSystem.HAULDESK: HaulDeskIngestionCoordinator(),
        SourceSystem.BROKEROS: BrokerOSIngestionCoordinator(),
    }[sync.binding.source_system]
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            return coordinator.ingest(
                session,
                SourceFile(sync.path, sync.path.read_bytes()),
                TenantContext(sync.binding.tenant_id),
            )
    finally:
        engine.dispose()


def _result_message(result: IngestionResult) -> str:
    if result.report is None:
        return json.dumps(
            {"versions_inserted": result.versions_created, "no_op": result.duplicate},
            sort_keys=True,
        )
    return result.report.render()


def main() -> None:
    """Run the Carrier Pool command-line interface."""
    app()
