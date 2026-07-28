"""Command-line entry point for Carrier Pool backend operations."""

import json
import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Load
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.backtest import RateBacktestHarness, write_backtest_artifacts
from carrier_pool.decisioning.carrier_scoring import (
    V6_SHRINKAGE_STRENGTH,
    CarrierHistoricalFitScorer,
)
from carrier_pool.decisioning.decision_runs import DecisionRunService
from carrier_pool.decisioning.ranking_evaluation import (
    RankingBacktestHarness,
    ranking_acceptance_failures,
    write_ranking_artifacts,
    write_ranking_formula_comparison,
)
from carrier_pool.demo import DEMO_TENANTS, seed_demo_tenants
from carrier_pool.domain.types import LoadStatus, SourceSystem
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


@app.command("seed-demo-tenants")
def seed_demo() -> None:
    """Create the three fixed fictional brokers used by the review demo."""
    database_url = _database_url("seeding demo tenants")
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            tenants = seed_demo_tenants(session)
            session.commit()
    finally:
        engine.dispose()
    typer.echo(f"seeded {len(tenants)} demo tenants")


@app.command("decide-active")
def decide_active() -> None:
    """Persist or reuse decisions for every active Day 11 demo load."""
    database_url = _database_url("computing demo decisions")
    engine = create_engine(database_url)
    created = 0
    reused = 0
    try:
        with Session(engine) as session:
            service = DecisionRunService()
            for tenant in DEMO_TENANTS:
                set_tenant_context(session, tenant.id)
                loads = session.scalars(
                    select(Load).where(
                        Load.tenant_id == tenant.id,
                        Load.status == LoadStatus.ACTIVE,
                    )
                ).all()
                for load in loads:
                    result = service.run(session, tenant.id, load.id, load.observed_at)
                    reused += int(result.reused)
                    created += int(not result.reused)
            session.commit()
    finally:
        engine.dispose()
    typer.echo(f"decisions created={created} reused={reused}")


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


@app.command("rate-backtest")
def rate_backtest(
    artifacts_dir: Annotated[Path, typer.Option("--artifacts-dir", file_okay=False)] = Path(
        "../artifacts"
    ),
) -> None:
    """Run leakage-safe historical rate evaluation and write review artifacts."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise typer.BadParameter("DATABASE_URL is required for rate backtesting.")
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            report = RateBacktestHarness().run(session)
            ranking_report = RankingBacktestHarness(
                scorer=CarrierHistoricalFitScorer(history_mode="identity")
            ).run(session)
            legacy_ranking_report = RankingBacktestHarness(
                scorer=CarrierHistoricalFitScorer(history_mode="legacy")
            ).run(session)
            calibrated_ranking_report = RankingBacktestHarness(
                scorer=CarrierHistoricalFitScorer(
                    history_mode="identity", shrinkage_strength=V6_SHRINKAGE_STRENGTH
                )
            ).run(session)
    finally:
        engine.dispose()
    metrics_path, cases_path = write_backtest_artifacts(report, artifacts_dir)
    ranking_metrics_path = write_ranking_artifacts(ranking_report, artifacts_dir)
    ranking_comparison_path = write_ranking_formula_comparison(
        ranking_report, legacy_ranking_report, artifacts_dir, calibrated_ranking_report
    )
    ranking_failures = ranking_acceptance_failures(ranking_report)
    if ranking_failures:
        typer.echo("ranking evaluation acceptance failed: " + "; ".join(ranking_failures))
        raise typer.Exit(code=1)
    typer.echo(
        json.dumps(
            {
                "case_count": report.case_count,
                "scored_case_count": report.scored_case_count,
                "metrics_path": str(metrics_path),
                "cases_path": str(cases_path),
                "ranking_metrics_path": str(ranking_metrics_path),
                "ranking_comparison_path": str(ranking_comparison_path),
            },
            sort_keys=True,
        )
    )


def _ingest_discovered(sync: DiscoveredSync) -> IngestionResult:
    database_url = _database_url("ingestion")
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


def _database_url(operation: str) -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise typer.BadParameter(f"DATABASE_URL is required for {operation}.")
    return database_url


def main() -> None:
    """Run the Carrier Pool command-line interface."""
    app()
