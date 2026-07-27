"""PostgreSQL integration tests for BrokerOS restated-rate persistence."""

import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from test_brokeros_parser import LOAD_ID, _payload

from carrier_pool.db.models import Load, LoadVersion, SourceRateEntry, Tenant
from carrier_pool.domain.types import SourceSystem
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import BrokerOSIngestionCoordinator

DATABASE_URL = os.getenv("DATABASE_URL")
CARRIER_ID = "0011I00000AbCdEQAZ"
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


@pytest.fixture
def session() -> Session:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    with Session(engine) as database_session:
        yield database_session
        database_session.rollback()
    engine.dispose()


def _source_file(name: str, synced_at: str, carrier_rate: int) -> SourceFile:
    payload = _payload()
    payload["synced_at"] = synced_at
    records = payload["records"]
    references = payload["referenced_records"]
    assert isinstance(records, list)
    assert isinstance(references, dict)
    records[0]["bos__Load_Status__c"] = "Booked"
    records[0]["bos__Carrier__c"] = CARRIER_ID
    records[0]["bos__Carrier_Rate__c"] = carrier_rate
    records[0]["LastModifiedDate"] = synced_at
    references[CARRIER_ID] = {"type": "Account", "record_type": "Carrier", "Name": "Carrier"}
    return SourceFile(Path(name), json.dumps(payload).encode())


def test_brokeros_restated_rate_creates_new_version_and_replaces_projection(
    session: Session,
) -> None:
    tenant_id = uuid4()
    tenant = Tenant(
        id=tenant_id,
        slug=f"brokeros-restatement-{uuid4()}",
        name="BrokerOS Restatement",
        source_system=SourceSystem.BROKEROS,
    )
    session.add(tenant)
    session.commit()

    coordinator = BrokerOSIngestionCoordinator()
    context = TenantContext(str(tenant_id))
    initial = _source_file("initial.json", "2026-07-06T11:00:00.000+0000", 1400)
    restated = _source_file("restated.json", "2026-07-06T17:00:00.000+0000", 1450)

    assert coordinator.ingest(session, initial, context).versions_created == 1
    assert coordinator.ingest(session, restated, context).versions_created == 1

    versions = session.scalars(
        select(LoadVersion.carrier_rate_amount)
        .where(LoadVersion.tenant_id == tenant.id)
        .order_by(LoadVersion.observed_at)
    ).all()
    current_rate = session.scalar(
        select(Load.carrier_rate_amount).where(
            Load.tenant_id == tenant.id,
            Load.source_system == SourceSystem.BROKEROS,
            Load.external_id == LOAD_ID,
        )
    )
    ledger_rows = session.scalar(
        select(func.count())
        .select_from(SourceRateEntry)
        .where(SourceRateEntry.tenant_id == tenant.id)
    )
    canonical_snapshot = session.scalar(
        select(LoadVersion.canonical_snapshot)
        .where(LoadVersion.tenant_id == tenant.id)
        .order_by(LoadVersion.observed_at.desc())
    )

    assert versions == [Decimal("1400.00"), Decimal("1450.00")]
    assert current_rate == Decimal("1450.00")
    assert ledger_rows == 0
    assert canonical_snapshot is not None
    assert canonical_snapshot["stops"][0]["planned_date"] == "2026-07-07"
    assert canonical_snapshot["cargo_items"][0]["commodity"] == "Packaged foods"
    assert canonical_snapshot["cargo_items"][0]["declared_weight_unit"] == "lbs"
