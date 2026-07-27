"""Integration coverage for immutable current-projection rebuilds."""

import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session

from carrier_pool.db.models import Customer, Load, SourceRateEntry, Stop, Tenant
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.domain.types import LoadStatus, SourceSystem
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import HaulDeskIngestionCoordinator
from carrier_pool.ingestion.rebuild import rebuild_current_projections

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


def _source_file(
    name: str, sync_at: str, rates: list[dict[str, object]], *, include_load: bool
) -> SourceFile:
    payload: dict[str, object] = {
        "synced_at": sync_at,
        "loads": [
            {
                "load_num": "HD-REBUILD-1",
                "status_code": 30,
                "customer_code": "C-REBUILD",
                "customer_name": "Rebuild Customer",
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
        "load_num": "HD-REBUILD-1",
        "side": side,
        "code": code,
        "amount_usd": amount,
        "created_at": "2026-07-06 03:00:00",
    }


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
            {
                "external_id": load.external_id,
                "customer_id": str(load.customer_id),
                "customer_name": load.customer.name,
                "status": load.status.value,
                "equipment": None if load.equipment is None else load.equipment.value,
                "customer_rate": str(load.customer_rate_amount),
                "carrier_rate": str(load.carrier_rate_amount),
                "source_created": load.source_created_at.isoformat(),
                "source_modified": load.source_modified_at.isoformat(),
                "observed": load.observed_at.isoformat(),
                "current_version": str(load.current_version_id),
                "stops": [
                    (
                        stop.sequence,
                        stop.city,
                        stop.state,
                        stop.postal_code,
                        stop.is_pickup,
                        stop.is_dropoff,
                    )
                    for stop in stops
                ],
            }
        )
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def test_rebuild_matches_incremental_projection_state() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    tenant_id = uuid4()
    try:
        with Session(engine) as session:
            session.add(
                Tenant(
                    id=tenant_id,
                    slug=f"rebuild-{uuid4()}",
                    name="Rebuild",
                    source_system=SourceSystem.HAULDESK,
                )
            )
            session.commit()
            coordinator = HaulDeskIngestionCoordinator()
            context = TenantContext(str(tenant_id))
            coordinator.ingest(
                session,
                _source_file(
                    "initial.json",
                    "2026-07-06 06:00:00",
                    [_rate(1, 1300, "LINEHAUL", "bill"), _rate(2, 1000, "LINEHAUL")],
                    include_load=True,
                ),
                context,
            )
            coordinator.ingest(
                session,
                _source_file(
                    "adjustment.json",
                    "2026-07-06 12:00:00",
                    [_rate(3, -20, "ADJUSTMENT")],
                    include_load=False,
                ),
                context,
            )
            expected = _state_hash(session, tenant_id)
            assert session.scalar(
                select(SourceRateEntry.amount).where(
                    SourceRateEntry.tenant_id == tenant_id, SourceRateEntry.external_id == "3"
                )
            ) == Decimal("-20.00")
            session.rollback()

            with session.begin():
                set_tenant_context(session, tenant_id)
                session.execute(delete(Stop).where(Stop.tenant_id == tenant_id))
                session.execute(
                    update(Load)
                    .where(Load.tenant_id == tenant_id)
                    .values(
                        status=LoadStatus.PLANNED,
                        customer_rate_amount=Decimal("1"),
                        carrier_rate_amount=Decimal("1"),
                        weight_lbs=Decimal("1"),
                        distance_miles=Decimal("1"),
                        source_created_at=datetime(2026, 1, 1, tzinfo=UTC),
                        source_modified_at=datetime(2026, 1, 1, tzinfo=UTC),
                        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                        current_version_id=None,
                    )
                )
                session.execute(
                    update(Customer).where(Customer.tenant_id == tenant_id).values(name="corrupt")
                )

            result = rebuild_current_projections(session, tenant_id)
            assert (result.loads_rebuilt, result.stops_rebuilt) == (1, 2)
            assert _state_hash(session, tenant_id) == expected
    finally:
        engine.dispose()
