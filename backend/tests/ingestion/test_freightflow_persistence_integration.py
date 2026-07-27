"""PostgreSQL integration test for supplied FreightFlow replacement snapshots."""

import json
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Load, LoadVersion, Tenant
from carrier_pool.domain.types import LoadStatus, SourceSystem
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import FreightFlowIngestionCoordinator

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


def _source_example(name: str) -> SourceFile:
    path = Path(__file__).parents[3] / "data" / "tms_a_freightflow" / name
    content = re.sub(r"//.*$", "", path.read_text(), flags=re.MULTILINE)
    return SourceFile(path, json.dumps(json.loads(content)).encode())


def test_freightflow_replacement_snapshots_preserve_history_and_are_idempotent() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    tenant_id = uuid4()
    try:
        with Session(engine) as session:
            tenant = Tenant(
                id=tenant_id,
                slug=f"freightflow-history-{uuid4()}",
                name="FreightFlow History",
                source_system=SourceSystem.FREIGHTFLOW,
            )
            session.add(tenant)
            session.commit()

            coordinator = FreightFlowIngestionCoordinator()
            context = TenantContext(str(tenant_id))
            initial = _source_example("example_sync.jsonc")
            later = _source_example("example_sync_next.jsonc")

            assert coordinator.ingest(session, initial, context).versions_created == 1
            assert coordinator.ingest(session, later, context).versions_created == 1
            assert coordinator.ingest(session, later, context).duplicate is True

            versions = session.scalars(
                select(LoadVersion.status)
                .where(LoadVersion.tenant_id == tenant_id)
                .order_by(LoadVersion.observed_at)
            ).all()
            current_load = session.scalar(
                select(Load).where(Load.tenant_id == tenant_id, Load.external_id == "127472397")
            )

            assert versions == [LoadStatus.ACTIVE, LoadStatus.COVERED]
            assert current_load is not None
            assert current_load.status is LoadStatus.COVERED
            assert current_load.current_version is not None
            assert current_load.current_version.status is LoadStatus.COVERED
    finally:
        engine.dispose()
