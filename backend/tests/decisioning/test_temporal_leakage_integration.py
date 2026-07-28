"""Regression coverage for future assignment, rate, and correction leakage."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    Carrier,
    Customer,
    IngestionFile,
    IngestionStatus,
    Load,
    LoadVersion,
    Tenant,
)
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.backtest import RateBacktestHarness
from carrier_pool.decisioning.carrier_features import CarrierFeatureService
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFitScorer
from carrier_pool.decisioning.decision_runs import DecisionRunService
from carrier_pool.decisioning.pricing import HierarchicalRateEstimator
from carrier_pool.domain.types import EquipmentType, LoadStatus, SourceSystem
from carrier_pool.geography.comparables import ComparableLoadRepository

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


def _snapshot(external_id: str) -> dict[str, object]:
    return {
        "external_id": external_id,
        "stops": [
            {
                "sequence": 1,
                "is_pickup": True,
                "is_dropoff": False,
                "city": "Grand Prairie",
                "state": "TX",
                "postal_code": "75050",
            },
            {
                "sequence": 2,
                "is_pickup": False,
                "is_dropoff": True,
                "city": "Katy",
                "state": "TX",
                "postal_code": "77449",
            },
        ],
    }


def _ingestion(session: Session, tenant: Tenant, observed_at: datetime) -> IngestionFile:
    ingestion = IngestionFile(
        tenant_id=tenant.id,
        source_system=SourceSystem.FREIGHTFLOW,
        relative_path="temporal-test",
        file_name=f"{uuid4()}.json",
        sha256=uuid4().hex * 2,
        raw_payload={},
        sync_at=observed_at,
        observed_at=observed_at,
        status=IngestionStatus.COMPLETED,
        started_at=observed_at,
        completed_at=observed_at,
    )
    session.add(ingestion)
    session.flush()
    return ingestion


def _version(
    session: Session,
    tenant: Tenant,
    load: Load,
    customer: Customer,
    observed_at: datetime,
    status: LoadStatus,
    carrier: Carrier | None,
    rate: Decimal | None,
) -> LoadVersion:
    version = LoadVersion(
        tenant_id=tenant.id,
        load_id=load.id,
        ingestion_file_id=_ingestion(session, tenant, observed_at).id,
        source_modified_at=observed_at,
        observed_at=observed_at,
        status=status,
        equipment=EquipmentType.DRY_VAN,
        customer_id=customer.id,
        carrier_id=None if carrier is None else carrier.id,
        customer_rate_amount=Decimal("2000"),
        carrier_rate_amount=rate,
        currency="USD",
        weight_lbs=Decimal("24000"),
        distance_miles=Decimal("239.4"),
        canonical_snapshot=_snapshot(load.external_id),
        raw_snapshot={},
        snapshot_hash=uuid4().hex * 2,
    )
    session.add(version)
    session.flush()
    return version


def _load(
    session: Session, tenant: Tenant, customer: Customer, external_id: str, observed_at: datetime
) -> Load:
    load = Load(
        tenant_id=tenant.id,
        source_system=SourceSystem.FREIGHTFLOW,
        external_id=external_id,
        customer_id=customer.id,
        status=LoadStatus.PLANNED,
        equipment=EquipmentType.DRY_VAN,
        currency="USD",
        source_created_at=observed_at,
        source_modified_at=observed_at,
        observed_at=observed_at,
    )
    session.add(load)
    session.flush()
    return load


def _set_current(load: Load, version: LoadVersion) -> None:
    load.carrier_id = version.carrier_id
    load.status = version.status
    load.customer_rate_amount = version.customer_rate_amount
    load.carrier_rate_amount = version.carrier_rate_amount
    load.source_modified_at = version.source_modified_at
    load.observed_at = version.observed_at
    load.current_version_id = version.id


def test_future_assignment_rate_and_correction_never_enter_historical_decision_or_backtest() -> (
    None
):
    """Present projections may be future-aware; historical decision inputs must not be."""
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    cutoff = datetime(2026, 7, 5, 6, tzinfo=UTC)
    historical_at = cutoff - timedelta(days=1)
    future_at = cutoff + timedelta(days=1)
    try:
        with Session(engine) as session:
            tenant = Tenant(
                id=uuid4(),
                slug=f"temporal-leak-{uuid4()}",
                name="Temporal leakage test",
                source_system=SourceSystem.FREIGHTFLOW,
            )
            session.add(tenant)
            session.commit()
            set_tenant_context(session, tenant.id)
            customer = Customer(
                tenant_id=tenant.id,
                source_system=SourceSystem.FREIGHTFLOW,
                external_id="customer",
                name="Customer",
                first_observed_at=historical_at,
                last_observed_at=future_at,
            )
            carrier_a = Carrier(
                tenant_id=tenant.id,
                source_system=SourceSystem.FREIGHTFLOW,
                external_id="carrier-a",
                name="Carrier A",
                normalized_name="CARRIER A",
                first_observed_at=historical_at,
                last_observed_at=future_at,
            )
            carrier_b = Carrier(
                tenant_id=tenant.id,
                source_system=SourceSystem.FREIGHTFLOW,
                external_id="carrier-b",
                name="Carrier B",
                normalized_name="CARRIER B",
                first_observed_at=future_at,
                last_observed_at=future_at,
            )
            session.add_all((customer, carrier_a, carrier_b))
            session.flush()

            historical_load = _load(session, tenant, customer, "history", historical_at)
            historical_version = _version(
                session,
                tenant,
                historical_load,
                customer,
                historical_at,
                LoadStatus.COMPLETED,
                carrier_a,
                Decimal("1000"),
            )
            future_correction = _version(
                session,
                tenant,
                historical_load,
                customer,
                future_at,
                LoadStatus.COMPLETED,
                carrier_b,
                Decimal("2500"),
            )
            _set_current(historical_load, future_correction)

            target_load = _load(session, tenant, customer, "target", cutoff)
            active_version = _version(
                session,
                tenant,
                target_load,
                customer,
                cutoff,
                LoadStatus.ACTIVE,
                None,
                None,
            )
            final_target = _version(
                session,
                tenant,
                target_load,
                customer,
                future_at + timedelta(hours=6),
                LoadStatus.COMPLETED,
                carrier_a,
                Decimal("1700"),
            )
            _set_current(target_load, final_target)
            session.commit()

            comparable = ComparableLoadRepository().retrieve(
                session, tenant.id, target_load.id, active_version.id, cutoff
            )
            assert [item.version_id for item in comparable] == [historical_version.id]

            estimate = HierarchicalRateEstimator().estimate(
                session, tenant.id, target_load.id, cutoff
            )
            assert estimate.point_estimate_usd == Decimal("1000")
            assert [item.load_version_id for item in estimate.comparables] == [
                historical_version.id
            ]
            assert future_correction.id not in {
                item.load_version_id for item in estimate.comparables
            }

            features = CarrierFeatureService().retrieve(
                session, tenant.id, target_load.id, active_version.id, cutoff
            )
            assert [item.carrier_external_id for item in features] == ["carrier-a"]
            assert (
                CarrierHistoricalFitScorer().score(features)[0].carrier_external_id == "carrier-a"
            )

            decision = DecisionRunService().run(session, tenant.id, target_load.id, cutoff)
            session.commit()
            pricing_evidence = decision.run.evidence_summary["pricing_evidence_ids"]
            assert str(historical_version.id) in pricing_evidence
            assert str(future_correction.id) not in pricing_evidence

            report = RateBacktestHarness().run(session, (tenant.id,))
            target_case = next(case for case in report.cases if case.case.load_id == target_load.id)
            assert target_case.actual_carrier_rate_usd == Decimal("1700")
            assert [item.load_version_id for item in target_case.estimate.comparables] == [
                historical_version.id
            ]
    finally:
        engine.dispose()
