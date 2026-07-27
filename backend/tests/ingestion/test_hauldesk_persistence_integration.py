"""PostgreSQL integration tests for HaulDesk append-only ledger persistence."""

import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import LoadVersion, SourceRateEntry, Tenant
from carrier_pool.domain.types import SourceSystem
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import HaulDeskIngestionCoordinator

DATABASE_URL = os.getenv("DATABASE_URL")
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


def _source_file(
    name: str, sync_at: str, rates: list[dict[str, object]], *, include_load: bool
) -> SourceFile:
    payload: dict[str, object] = {
        "synced_at": sync_at,
        "loads": [
            {
                "load_num": "HD-1",
                "status_code": 30,
                "customer_code": "C-1",
                "customer_name": "Customer",
                "carrier_ref": None,
                "equip": "V",
                "weight_kg": 1000,
                "dist_km": 100,
                "pu_city": "Dallas",
                "pu_state": "TX",
                "pu_zip": "75201",
                "pu_date": "2026-07-07",
                "pu_departed_at": None,
                "del_city": "Austin",
                "del_state": "TX",
                "del_zip": "78701",
                "del_date": "2026-07-08",
                "del_arrived_at": None,
                "entered_at": "2026-07-05 14:00:00",
                "updated_at": "2026-07-06 03:00:00",
            }
        ]
        if include_load
        else [],
        "carriers": [],
        "rates": rates,
    }
    return SourceFile(Path(name), json.dumps(payload).encode())


def _rate(rate_id: int, amount: int, code: str, side: str = "pay") -> dict[str, object]:
    return {
        "rate_id": rate_id,
        "load_num": "HD-1",
        "side": side,
        "code": code,
        "amount_usd": amount,
        "created_at": "2026-07-06 03:00:00",
    }


def test_hauldesk_ledger_updates_current_total_once_and_preserves_versions(
    session: Session,
) -> None:
    tenant_id = uuid4()
    tenant = Tenant(
        id=tenant_id,
        slug=f"hauldesk-ledger-{uuid4()}",
        name="HaulDesk Ledger",
        source_system=SourceSystem.HAULDESK,
    )
    session.add(tenant)
    session.commit()

    coordinator = HaulDeskIngestionCoordinator()
    context = TenantContext(str(tenant_id))
    initial = _source_file(
        "initial.json",
        "2026-07-06 06:00:00",
        [_rate(10, 1300, "LINEHAUL", "bill"), _rate(1, 1000, "LINEHAUL")],
        include_load=True,
    )
    surcharge = _source_file(
        "surcharge.json",
        "2026-07-06 12:00:00",
        [_rate(2, 50, "FUEL")],
        include_load=False,
    )
    adjustment = _source_file(
        "adjustment.json",
        "2026-07-06 18:00:00",
        [_rate(3, -20, "ADJUSTMENT")],
        include_load=False,
    )

    assert coordinator.ingest(session, initial, context).versions_created == 1
    assert coordinator.ingest(session, surcharge, context).versions_created == 1
    assert coordinator.ingest(session, adjustment, context).versions_created == 1
    assert coordinator.ingest(session, adjustment, context).duplicate is True

    version_totals = session.execute(
        select(LoadVersion.customer_rate_amount, LoadVersion.carrier_rate_amount)
        .where(LoadVersion.tenant_id == tenant.id)
        .order_by(LoadVersion.observed_at)
    ).all()
    rate_count = session.scalar(
        select(func.count())
        .select_from(SourceRateEntry)
        .where(SourceRateEntry.tenant_id == tenant.id)
    )

    assert version_totals == [
        (Decimal("1300.00"), Decimal("1000.00")),
        (Decimal("1300.00"), Decimal("1050.00")),
        (Decimal("1300.00"), Decimal("1030.00")),
    ]
    assert rate_count == 4
