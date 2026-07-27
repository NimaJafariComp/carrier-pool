"""Phase 7 smoke: generated data remains ingestible, idempotent, and rebuildable."""

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import IngestionFile, Load, Stop, Tenant
from carrier_pool.domain.types import SourceSystem
from carrier_pool.generator.manifest import write_scenarios_manifest
from carrier_pool.generator.scheduler import write_sync_files
from carrier_pool.generator.validator import validate_generated_data
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import (
    BrokerOSIngestionCoordinator,
    FreightFlowIngestionCoordinator,
    HaulDeskIngestionCoordinator,
)
from carrier_pool.ingestion.discovery import FileIngestionOrchestrator, SourceBinding
from carrier_pool.ingestion.rebuild import rebuild_current_projections
from carrier_pool.geography.comparables import ComparableLoadRepository, LaneTier

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


def _state_hash(session: Session, tenant_id: UUID) -> str:
    loads = session.execute(
        select(Load).where(Load.tenant_id == tenant_id).order_by(Load.external_id)
    ).scalars()
    state = []
    for load in loads:
        stops = session.execute(
            select(Stop).where(Stop.load_id == load.id).order_by(Stop.sequence)
        ).scalars()
        state.append(
            (
                load.external_id,
                load.status.value,
                str(load.customer_rate_amount),
                str(load.carrier_rate_amount),
                str(load.current_version_id),
                [(stop.sequence, stop.postal_code) for stop in stops],
            )
        )
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()


def test_generated_data_ingests_idempotently_and_rebuilds(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    data_root = tmp_path / "data"
    assert len(write_sync_files(data_root)) == 123
    write_scenarios_manifest(data_root)
    assert validate_generated_data(data_root).sync_file_count == 123

    engine = create_engine(DATABASE_URL)
    tenant_ids = {source: uuid4() for source in SourceSystem}
    try:
        with Session(engine) as session:
            session.add_all(
                Tenant(
                    id=tenant_id,
                    slug=f"smoke-{source.value.lower()}-{uuid4()}",
                    name=f"Smoke {source.value}",
                    source_system=source,
                )
                for source, tenant_id in tenant_ids.items()
            )
            session.commit()
            coordinators = {
                SourceSystem.FREIGHTFLOW: FreightFlowIngestionCoordinator(),
                SourceSystem.HAULDESK: HaulDeskIngestionCoordinator(),
                SourceSystem.BROKEROS: BrokerOSIngestionCoordinator(),
            }
            directories = {
                SourceSystem.FREIGHTFLOW: "tms_a_freightflow",
                SourceSystem.HAULDESK: "tms_b_hauldesk",
                SourceSystem.BROKEROS: "tms_c_brokeros",
            }
            orchestrator = FileIngestionOrchestrator(
                tuple(
                    SourceBinding(data_root / directories[source], str(tenant_id), source)
                    for source, tenant_id in tenant_ids.items()
                )
            )

            def ingest(sync):
                return coordinators[sync.binding.source_system].ingest(
                    session,
                    SourceFile(sync.path, sync.path.read_bytes()),
                    TenantContext(sync.binding.tenant_id),
                )

            first = orchestrator.ingest_all(ingest)
            assert len(first) == 123
            assert any(result.versions_created > 0 for result in first)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IngestionFile)
                    .where(IngestionFile.tenant_id.in_(tenant_ids.values()))
                )
                == 123
            )
            day11_target = session.scalar(
                select(Load).where(
                    Load.tenant_id == tenant_ids[SourceSystem.HAULDESK],
                    Load.external_id == "HD-9001",
                )
            )
            assert day11_target is not None
            assert day11_target.current_version is not None
            day11_evidence = ComparableLoadRepository().retrieve(
                session,
                tenant_ids[SourceSystem.HAULDESK],
                day11_target.id,
                day11_target.current_version.id,
                day11_target.observed_at,
            )
            assert any(item.load_external_id == "HD-2101" for item in day11_evidence)
            assert all(item.tier is not LaneTier.TENANT_ALL_EQUIPMENT for item in day11_evidence)
            before = {
                tenant_id: _state_hash(session, tenant_id) for tenant_id in tenant_ids.values()
            }
            session.rollback()

            second = orchestrator.ingest_all(ingest)
            assert len(second) == 123
            assert all(result.duplicate for result in second)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IngestionFile)
                    .where(IngestionFile.tenant_id.in_(tenant_ids.values()))
                )
                == 123
            )
            session.rollback()

            for tenant_id, expected in before.items():
                rebuild_current_projections(session, tenant_id)
                assert _state_hash(session, tenant_id) == expected
                session.rollback()
    finally:
        engine.dispose()
