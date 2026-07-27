"""Tenant-scoped, as-of comparable-load retrieval integration coverage."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    Customer,
    IngestionFile,
    IngestionStatus,
    Load,
    LoadVersion,
    Tenant,
)
from carrier_pool.domain.types import EquipmentType, LoadStatus, SourceSystem
from carrier_pool.geography.comparables import ComparableLoadRepository, LaneTier
from carrier_pool.geography.lookup import GeographyLookup, GeographyResult

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


def _snapshot(origin_zip: str, destination_zip: str) -> dict[str, object]:
    return {
        "stops": [
            {
                "sequence": 1,
                "is_pickup": True,
                "is_dropoff": False,
                "city": "Origin",
                "state": "TX",
                "postal_code": origin_zip,
            },
            {
                "sequence": 2,
                "is_pickup": False,
                "is_dropoff": True,
                "city": "Destination",
                "state": "TX",
                "postal_code": destination_zip,
            },
        ]
    }


def _add_version(
    session: Session,
    tenant: Tenant,
    customer: Customer,
    external_id: str,
    status: LoadStatus,
    observed_at: datetime,
    origin_zip: str,
    destination_zip: str,
    *,
    equipment: EquipmentType = EquipmentType.DRY_VAN,
    distance_miles: Decimal = Decimal("240"),
) -> tuple[Load, LoadVersion]:
    ingestion = IngestionFile(
        tenant=tenant,
        source_system=SourceSystem.FREIGHTFLOW,
        relative_path="data",
        file_name=f"{external_id}-{observed_at:%H}.json",
        sha256=uuid4().hex * 2,
        raw_payload={},
        sync_at=observed_at,
        observed_at=observed_at,
        status=IngestionStatus.COMPLETED,
        started_at=observed_at,
        completed_at=observed_at,
    )
    load = Load(
        tenant=tenant,
        source_system=SourceSystem.FREIGHTFLOW,
        external_id=external_id,
        customer=customer,
        status=status,
        equipment=equipment,
        distance_miles=distance_miles,
        source_created_at=observed_at,
        source_modified_at=observed_at,
        observed_at=observed_at,
    )
    session.add_all((ingestion, load))
    session.flush()
    version = LoadVersion(
        tenant=tenant,
        load=load,
        ingestion_file=ingestion,
        source_modified_at=observed_at,
        observed_at=observed_at,
        status=status,
        equipment=equipment,
        customer=customer,
        distance_miles=distance_miles,
        canonical_snapshot={"external_id": external_id, **_snapshot(origin_zip, destination_zip)},
        raw_snapshot={},
        snapshot_hash=uuid4().hex * 2,
    )
    session.add(version)
    session.flush()
    load.current_version = version
    return load, version


def _tenant_with_customer(
    session: Session, source: SourceSystem = SourceSystem.FREIGHTFLOW
) -> tuple[Tenant, Customer]:
    now = datetime(2026, 7, 1, tzinfo=UTC)
    tenant = Tenant(
        id=uuid4(), slug=f"comparable-{uuid4()}", name="Comparable", source_system=source
    )
    customer = Customer(
        tenant=tenant,
        source_system=source,
        external_id=f"customer-{uuid4()}",
        name="Customer",
        first_observed_at=now,
        last_observed_at=now,
    )
    session.add_all((tenant, customer))
    session.flush()
    return tenant, customer


def test_retrieval_uses_suburb_history_excludes_reverse_future_and_other_tenant() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    now = datetime(2026, 7, 11, tzinfo=UTC)
    try:
        with Session(engine) as session:
            tenant, customer = _tenant_with_customer(session)
            other_tenant, other_customer = _tenant_with_customer(session)
            target_load, target_version = _add_version(
                session, tenant, customer, "target", LoadStatus.ACTIVE, now, "75050", "77449"
            )
            _add_version(
                session,
                tenant,
                customer,
                "suburb",
                LoadStatus.COMPLETED,
                now - timedelta(days=2),
                "75039",
                "77478",
            )
            _add_version(
                session,
                tenant,
                customer,
                "reverse",
                LoadStatus.COMPLETED,
                now - timedelta(days=2),
                "77449",
                "75050",
            )
            _add_version(
                session,
                tenant,
                customer,
                "future",
                LoadStatus.COMPLETED,
                now + timedelta(days=1),
                "75050",
                "77449",
            )
            _add_version(
                session,
                other_tenant,
                other_customer,
                "other-tenant",
                LoadStatus.COMPLETED,
                now - timedelta(days=1),
                "75050",
                "77449",
            )
            session.commit()

            evidence = ComparableLoadRepository().retrieve(
                session, tenant.id, target_load.id, target_version.id, now
            )

            assert [item.load_external_id for item in evidence] == ["suburb"]
            assert evidence[0].tier is LaneTier.NEAR_EXACT
            assert evidence[0].origin_distance_miles is not None
            assert evidence[0].destination_distance_miles is not None
            assert evidence[0].route_mile_difference == Decimal("0")
            assert evidence[0].recency_days == pytest.approx(2)
            assert str(evidence[0].version_id) in evidence[0].evidence_ids
    finally:
        engine.dispose()


def test_retrieval_classifies_exact_before_metro_and_relaxes_equipment_only_at_final_tier() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    now = datetime(2026, 7, 11, tzinfo=UTC)
    try:
        with Session(engine) as session:
            tenant, customer = _tenant_with_customer(session)
            target_load, target_version = _add_version(
                session, tenant, customer, "target", LoadStatus.ACTIVE, now, "75050", "77449"
            )
            _add_version(
                session,
                tenant,
                customer,
                "exact",
                LoadStatus.COMPLETED,
                now - timedelta(days=1),
                "75050",
                "77449",
            )
            _add_version(
                session,
                tenant,
                customer,
                "other-equipment",
                LoadStatus.COMPLETED,
                now - timedelta(days=1),
                "75050",
                "77449",
                equipment=EquipmentType.REEFER,
            )
            session.commit()

            evidence = ComparableLoadRepository().retrieve(
                session, tenant.id, target_load.id, target_version.id, now
            )

            assert [(item.load_external_id, item.tier) for item in evidence] == [
                ("exact", LaneTier.NEAR_EXACT)
            ]
    finally:
        engine.dispose()


def test_retrieval_uses_metro_when_exact_and_regional_evidence_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Lookup:
        coordinates = {
            "75050": (Decimal("0"), Decimal("0"), "DFW"),
            "77449": (Decimal("0"), Decimal("10"), "HOUSTON"),
            "75039": (Decimal("1"), Decimal("0"), "DFW"),
            "77478": (Decimal("1"), Decimal("10"), "HOUSTON"),
        }

        def lookup(self, postal_code: str, city: str, state: str) -> GeographyResult:
            latitude, longitude, metro_group = self.coordinates[postal_code]
            return GeographyResult(postal_code, city, state, latitude, longitude, metro_group, ())

    monkeypatch.setattr(GeographyLookup, "default", classmethod(lambda _class: _Lookup()))
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    now = datetime(2026, 7, 11, tzinfo=UTC)
    try:
        with Session(engine) as session:
            tenant, customer = _tenant_with_customer(session)
            target_load, target_version = _add_version(
                session, tenant, customer, "target", LoadStatus.ACTIVE, now, "75050", "77449"
            )
            _add_version(
                session,
                tenant,
                customer,
                "metro",
                LoadStatus.COMPLETED,
                now - timedelta(days=1),
                "75039",
                "77478",
            )
            session.commit()

            metro = ComparableLoadRepository().retrieve(
                session, tenant.id, target_load.id, target_version.id, now
            )
            assert [(item.load_external_id, item.tier) for item in metro] == [
                ("metro", LaneTier.METRO_CORRIDOR)
            ]

            _add_version(
                session,
                tenant,
                customer,
                "exact",
                LoadStatus.COMPLETED,
                now - timedelta(days=1),
                "75050",
                "77449",
            )
            session.commit()
            exact = ComparableLoadRepository().retrieve(
                session, tenant.id, target_load.id, target_version.id, now
            )
            assert [(item.load_external_id, item.tier) for item in exact] == [
                ("exact", LaneTier.NEAR_EXACT)
            ]
    finally:
        engine.dispose()
