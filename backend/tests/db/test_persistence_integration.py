"""PostgreSQL integration tests for Phase 3.3 temporal persistence."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    Carrier,
    Customer,
    IngestionFile,
    IngestionStatus,
    Load,
    LoadVersion,
    Stop,
    Tenant,
)
from carrier_pool.domain.types import EquipmentType, LoadStatus, SourceSystem

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


def _observed_at() -> datetime:
    return datetime(2026, 7, 27, tzinfo=UTC)


def _tenant(slug: str) -> Tenant:
    return Tenant(slug=slug, name=slug, source_system=SourceSystem.FREIGHTFLOW)


def _ingestion_file(tenant: Tenant) -> IngestionFile:
    now = _observed_at()
    return IngestionFile(
        tenant=tenant,
        source_system=SourceSystem.FREIGHTFLOW,
        relative_path="data/tms_a_freightflow/2026-07-27T00-00_sync.json",
        file_name="2026-07-27T00-00_sync.json",
        sha256=uuid4().hex * 2,
        raw_payload={"loads": []},
        sync_at=now,
        observed_at=now,
        status=IngestionStatus.COMPLETED,
        started_at=now,
        completed_at=now,
    )


def test_source_identity_is_unique_per_tenant_and_source_system(session: Session) -> None:
    first_tenant = _tenant(f"first-{uuid4()}")
    second_tenant = _tenant(f"second-{uuid4()}")
    session.add_all(
        [
            Customer(
                tenant=first_tenant,
                source_system=SourceSystem.FREIGHTFLOW,
                external_id="customer-1",
                name="First Customer",
                first_observed_at=_observed_at(),
                last_observed_at=_observed_at(),
            ),
            Customer(
                tenant=second_tenant,
                source_system=SourceSystem.FREIGHTFLOW,
                external_id="customer-1",
                name="Second Customer",
                first_observed_at=_observed_at(),
                last_observed_at=_observed_at(),
            ),
        ]
    )
    session.commit()

    session.add(
        Customer(
            tenant_id=first_tenant.id,
            source_system=SourceSystem.FREIGHTFLOW,
            external_id="customer-1",
            name="Duplicate Customer",
            first_observed_at=_observed_at(),
            last_observed_at=_observed_at(),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_load_version_persists_decimal_and_current_version_link(session: Session) -> None:
    tenant = _tenant(f"tenant-{uuid4()}")
    customer = Customer(
        tenant=tenant,
        source_system=SourceSystem.FREIGHTFLOW,
        external_id="customer-1",
        name="Customer",
        first_observed_at=_observed_at(),
        last_observed_at=_observed_at(),
    )
    carrier = Carrier(
        tenant=tenant,
        source_system=SourceSystem.FREIGHTFLOW,
        external_id="carrier-1",
        name="Carrier",
        normalized_name="CARRIER",
        first_observed_at=_observed_at(),
        last_observed_at=_observed_at(),
    )
    ingestion_file = _ingestion_file(tenant)
    load = Load(
        tenant=tenant,
        source_system=SourceSystem.FREIGHTFLOW,
        external_id="load-1",
        customer=customer,
        carrier=carrier,
        status=LoadStatus.COVERED,
        equipment=EquipmentType.DRY_VAN,
        customer_rate_amount=Decimal("1450.00"),
        carrier_rate_amount=Decimal("1180.00"),
        weight_lbs=Decimal("24000.000"),
        distance_miles=Decimal("242.100"),
        source_created_at=_observed_at(),
        source_modified_at=_observed_at(),
        observed_at=_observed_at(),
    )
    session.add_all([tenant, customer, carrier, ingestion_file, load])
    session.flush()
    version = LoadVersion(
        tenant=tenant,
        load=load,
        ingestion_file=ingestion_file,
        source_modified_at=_observed_at(),
        observed_at=_observed_at(),
        status=LoadStatus.COVERED,
        equipment=EquipmentType.DRY_VAN,
        customer=customer,
        carrier=carrier,
        customer_rate_amount=Decimal("1450.00"),
        carrier_rate_amount=Decimal("1180.00"),
        weight_lbs=Decimal("24000.000"),
        distance_miles=Decimal("242.100"),
        canonical_snapshot={"external_id": "load-1"},
        raw_snapshot={"shipmentId": 1},
        snapshot_hash=uuid4().hex * 2,
    )
    session.add(version)
    session.flush()
    load.current_version = version
    session.add(
        Stop(
            tenant=tenant,
            load=load,
            sequence=1,
            is_pickup=True,
            is_dropoff=False,
            city="Dallas",
            state="TX",
            postal_code="75201",
        )
    )
    session.commit()

    session.refresh(version)
    session.refresh(load)
    assert version.carrier_rate_amount == Decimal("1180.00")
    assert load.current_version_id == version.id


def test_load_version_cannot_be_updated_after_insert(session: Session) -> None:
    tenant = _tenant(f"immutable-{uuid4()}")
    customer = Customer(
        tenant=tenant,
        source_system=SourceSystem.FREIGHTFLOW,
        external_id="customer-immutable",
        name="Customer",
        first_observed_at=_observed_at(),
        last_observed_at=_observed_at(),
    )
    ingestion_file = _ingestion_file(tenant)
    load = Load(
        tenant=tenant,
        source_system=SourceSystem.FREIGHTFLOW,
        external_id="load-immutable",
        customer=customer,
        status=LoadStatus.ACTIVE,
        source_created_at=_observed_at(),
        source_modified_at=_observed_at(),
        observed_at=_observed_at(),
    )
    session.add_all([tenant, customer, ingestion_file, load])
    session.flush()
    version = LoadVersion(
        tenant=tenant,
        load=load,
        ingestion_file=ingestion_file,
        source_modified_at=_observed_at(),
        observed_at=_observed_at(),
        status=LoadStatus.ACTIVE,
        canonical_snapshot={"status": "ACTIVE"},
        raw_snapshot={"status": "Booking"},
        snapshot_hash=uuid4().hex * 2,
    )
    session.add(version)
    session.commit()

    version.canonical_snapshot = {"status": "COVERED"}
    with pytest.raises(IntegrityError):
        session.commit()
