"""Phase 7.3 current-projection precedence integration tests."""

import json
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import IngestionFile, Load, LoadVersion, Tenant
from carrier_pool.domain.types import LoadStatus, SourceSystem
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import FreightFlowIngestionCoordinator

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


def _source(name: str, sync_at: str, status: str, modified_at: str) -> SourceFile:
    path = Path(__file__).parents[3] / "data" / "tms_a_freightflow" / "example_sync.jsonc"
    payload = json.loads(re.sub(r"//.*$", "", path.read_text(), flags=re.MULTILINE))
    payload["syncedAt"] = sync_at
    payload["loads"][0]["status"] = status
    payload["loads"][0]["createdDate"] = "2026-07-01T00:00:00-05:00"
    payload["loads"][0]["lastModifiedDate"] = modified_at
    return SourceFile(Path(name), json.dumps(payload).encode())


def test_precedence_keeps_stale_version_accepts_late_regression_and_skips_unchanged_snapshot() -> (
    None
):
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    tenant = Tenant(
        id=uuid4(),
        slug=f"precedence-{uuid4()}",
        name="Precedence Test",
        source_system=SourceSystem.FREIGHTFLOW,
    )
    context = TenantContext(str(tenant.id))
    try:
        with Session(engine) as session:
            session.add(tenant)
            session.commit()
            coordinator = FreightFlowIngestionCoordinator()

            current = _source(
                "2026-07-01T12-00_sync.json",
                "2026-07-01T12:00:00-05:00",
                "Dispatched",
                "2026-07-01T12:00:00-05:00",
            )
            stale = _source(
                "2026-07-01T06-00_sync.json",
                "2026-07-01T06:00:00-05:00",
                "Booking",
                "2026-07-01T06:00:00-05:00",
            )
            correction = _source(
                "2026-07-01T18-00_sync.json",
                "2026-07-01T18:00:00-05:00",
                "Booking",
                "2026-07-01T05:00:00-05:00",
            )
            unchanged = _source(
                "2026-07-02T00-00_sync.json",
                "2026-07-02T00:00:00-05:00",
                "Booking",
                "2026-07-01T05:00:00-05:00",
            )

            assert coordinator.ingest(session, current, context).versions_created == 1
            assert coordinator.ingest(session, stale, context).versions_created == 1
            assert coordinator.ingest(session, correction, context).versions_created == 1
            assert coordinator.ingest(session, unchanged, context).versions_created == 0

            load = session.scalar(select(Load).where(Load.tenant_id == tenant.id))
            assert load is not None
            assert load.status is LoadStatus.ACTIVE
            assert load.current_version is not None
            assert load.current_version.source_modified_at.isoformat().startswith(
                "2026-07-01T10:00"
            )
            assert (
                len(
                    session.scalars(
                        select(LoadVersion).where(LoadVersion.tenant_id == tenant.id)
                    ).all()
                )
                == 3
            )

            files = {
                record.file_name: record
                for record in session.scalars(
                    select(IngestionFile).where(IngestionFile.tenant_id == tenant.id)
                )
            }
            assert files[stale.path.name].warning_details == {
                "anomalies": [{"code": "OUT_OF_ORDER_SNAPSHOT"}]
            }
            assert files[correction.path.name].warning_details == {
                "anomalies": [{"code": "STATUS_REGRESSION_CORRECTION"}]
            }
            assert files[unchanged.path.name].versions_created == 0
    finally:
        engine.dispose()
