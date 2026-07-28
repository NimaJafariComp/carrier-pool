"""Black-box Phase 11 API contracts against deterministic generated data."""

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import carrier_pool.main as main_module
from carrier_pool.db.models import (
    Carrier,
    CarrierRecommendation,
    Customer,
    DecisionRun,
    IngestionFile,
    Load,
    LoadVersion,
    Tenant,
)
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.carrier_features import CarrierFeatureService
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFitScorer
from carrier_pool.decisioning.decision_runs import DecisionRunService
from carrier_pool.decisioning.pricing import HierarchicalRateEstimator
from carrier_pool.demo import DEMO_TENANTS, seed_demo_tenants
from carrier_pool.domain.types import EquipmentType, LoadStatus, SourceSystem
from carrier_pool.generator.scheduler import DAY11_SYNC_AT, write_sync_files
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import (
    BrokerOSIngestionCoordinator,
    FreightFlowIngestionCoordinator,
    HaulDeskIngestionCoordinator,
)
from carrier_pool.ingestion.discovery import FileIngestionOrchestrator, SourceBinding
from carrier_pool.main import app

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="DATABASE_URL is required")


@dataclass(frozen=True, slots=True)
class ApiDataset:
    tenant_ids: dict[SourceSystem, UUID]
    day11_load_ids: dict[SourceSystem, UUID]


def _serialized_prediction(session: Session, tenant_id: UUID, load_id: UUID) -> bytes:
    """Serialize the full production rate/ranking result for an isolation assertion."""
    set_tenant_context(session, tenant_id)
    load = session.scalar(select(Load).where(Load.tenant_id == tenant_id, Load.id == load_id))
    assert load is not None and load.current_version_id is not None
    estimate = HierarchicalRateEstimator().estimate(session, tenant_id, load.id, load.observed_at)
    features = CarrierFeatureService().retrieve(
        session, tenant_id, load.id, load.current_version_id, load.observed_at
    )
    rankings = CarrierHistoricalFitScorer().score(features)
    payload = {
        "pricing": {
            "point": (
                None if estimate.point_estimate_usd is None else str(estimate.point_estimate_usd)
            ),
            "lower": (
                None
                if estimate.historical_comparison_lower_usd is None
                else str(estimate.historical_comparison_lower_usd)
            ),
            "upper": (
                None
                if estimate.historical_comparison_upper_usd is None
                else str(estimate.historical_comparison_upper_usd)
            ),
            "tier": None if estimate.local_tier is None else estimate.local_tier.value,
            "comparables": [
                (str(item.load_version_id), str(item.carrier_rate_usd), item.tier.value)
                for item in estimate.comparables
            ],
        },
        "rankings": [
            {
                "carrier": item.carrier_external_id,
                "adjusted_score": str(item.adjusted_score),
                "confidence": str(item.confidence_score),
                "components": {
                    key: None if value is None else str(value)
                    for key, value in sorted(item.component_scores.items())
                },
                "evidence_status": item.evidence_status,
                "tie_group": item.tie_group,
            }
            for item in rankings
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _insert_additional_freightflow_history(session: Session, tenant_id: UUID) -> None:
    """Add valid completed Tenant A history after Tenant B's fixed cutoff."""
    set_tenant_context(session, tenant_id)
    customer = session.scalar(select(Customer).where(Customer.tenant_id == tenant_id))
    carrier = session.scalar(select(Carrier).where(Carrier.tenant_id == tenant_id))
    ingestion = session.scalar(
        select(IngestionFile).where(
            IngestionFile.tenant_id == tenant_id,
            IngestionFile.source_system == SourceSystem.FREIGHTFLOW,
        )
    )
    assert customer is not None and carrier is not None and ingestion is not None
    observed_at = datetime(2026, 7, 10, 18, tzinfo=UTC)
    external_id = f"isolation-{uuid4()}"
    load = Load(
        tenant_id=tenant_id,
        source_system=SourceSystem.FREIGHTFLOW,
        external_id=external_id,
        customer_id=customer.id,
        carrier_id=carrier.id,
        status=LoadStatus.COMPLETED,
        equipment=EquipmentType.DRY_VAN,
        customer_rate_amount=Decimal("1600.00"),
        carrier_rate_amount=Decimal("1250.00"),
        weight_lbs=Decimal("20000"),
        distance_miles=Decimal("240.0"),
        source_created_at=observed_at,
        source_modified_at=observed_at,
        observed_at=observed_at,
    )
    session.add(load)
    session.flush()
    version = LoadVersion(
        tenant_id=tenant_id,
        load_id=load.id,
        ingestion_file_id=ingestion.id,
        source_modified_at=observed_at,
        observed_at=observed_at,
        status=LoadStatus.COMPLETED,
        equipment=EquipmentType.DRY_VAN,
        customer_id=customer.id,
        carrier_id=carrier.id,
        customer_rate_amount=Decimal("1600.00"),
        carrier_rate_amount=Decimal("1250.00"),
        weight_lbs=Decimal("20000"),
        distance_miles=Decimal("240.0"),
        canonical_snapshot={"external_id": external_id, "stops": []},
        raw_snapshot={},
        snapshot_hash=f"isolation-{uuid4()}",
    )
    session.add(version)
    session.flush()
    load.current_version = version


def _insert_shared_authority_carrier(
    session: Session, tenant_id: UUID, source_system: SourceSystem
) -> Carrier:
    """Create one tenant-local carrier with the catalog's intentionally shared authority."""
    set_tenant_context(session, tenant_id)
    observed_at = datetime(2026, 7, 10, 18, tzinfo=UTC)
    carrier = Carrier(
        tenant_id=tenant_id,
        source_system=source_system,
        external_id=f"shared-authority-{uuid4()}",
        name="Crossroads Carrier",
        normalized_name="CROSSROADS CARRIER",
        mc_number="1350101",
        dot_number="3901001",
        first_observed_at=observed_at,
        last_observed_at=observed_at,
    )
    session.add(carrier)
    session.flush()
    return carrier


@pytest.fixture()
def api_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ApiDataset]:
    assert DATABASE_URL is not None
    data_root = tmp_path / "data"
    write_sync_files(data_root)
    engine = create_engine(DATABASE_URL)
    tenant_ids = {source: uuid4() for source in SourceSystem}
    monkeypatch.setattr(main_module, "DEMO_TENANT_IDS", frozenset(tenant_ids.values()))
    directories = {
        SourceSystem.FREIGHTFLOW: "tms_a_freightflow",
        SourceSystem.HAULDESK: "tms_b_hauldesk",
        SourceSystem.BROKEROS: "tms_c_brokeros",
    }
    coordinators = {
        SourceSystem.FREIGHTFLOW: FreightFlowIngestionCoordinator(),
        SourceSystem.HAULDESK: HaulDeskIngestionCoordinator(),
        SourceSystem.BROKEROS: BrokerOSIngestionCoordinator(),
    }
    try:
        with Session(engine) as session:
            seed_demo_tenants(session)
            session.add_all(
                Tenant(
                    id=tenant_id,
                    slug=f"api-contract-{source.value.lower()}-{uuid4()}",
                    name=f"API Contract {source.value}",
                    source_system=source,
                )
                for source, tenant_id in tenant_ids.items()
            )
            session.commit()
            bindings = tuple(
                SourceBinding(data_root / directories[source], str(tenant_id), source)
                for source, tenant_id in tenant_ids.items()
            )

            def ingest(sync: object) -> object:
                typed_sync = sync
                return coordinators[typed_sync.binding.source_system].ingest(
                    session,
                    SourceFile(typed_sync.path, typed_sync.path.read_bytes()),
                    TenantContext(typed_sync.binding.tenant_id),
                )

            FileIngestionOrchestrator(bindings).ingest_all(ingest)
            day11_load_ids: dict[SourceSystem, UUID] = {}
            for source, tenant_id in tenant_ids.items():
                set_tenant_context(session, tenant_id)
                load = session.scalar(
                    select(Load).where(
                        Load.tenant_id == tenant_id,
                        Load.source_system == source,
                        Load.observed_at == DAY11_SYNC_AT,
                    )
                )
                assert load is not None
                day11_load_ids[source] = load.id
                DecisionRunService().run(session, tenant_id, load.id, load.observed_at)
            session.commit()
        yield ApiDataset(tenant_ids, day11_load_ids)
    finally:
        engine.dispose()


def _headers(dataset: ApiDataset, source: SourceSystem) -> dict[str, str]:
    return {"X-Tenant-ID": str(dataset.tenant_ids[source])}


def test_tenant_directory_excludes_integration_fixture_tenants(api_dataset: ApiDataset) -> None:
    response = TestClient(app).get("/api/v1/tenants")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(tenant.id),
            "slug": tenant.slug,
            "name": tenant.name,
            "source_system": tenant.source_system.value,
        }
        for tenant in sorted(DEMO_TENANTS, key=lambda tenant: tenant.name)
    ]


def test_active_loads_and_decision_contract_are_tenant_scoped(api_dataset: ApiDataset) -> None:
    client = TestClient(app)
    source = SourceSystem.BROKEROS
    load_id = api_dataset.day11_load_ids[source]
    response = client.get("/api/v1/loads?status=ACTIVE", headers=_headers(api_dataset, source))
    assert response.status_code == 200
    assert response.json()
    assert all(item["status"] == "ACTIVE" for item in response.json())
    assert any(item["id"] == str(load_id) for item in response.json())

    decision = client.get(
        f"/api/v1/loads/{load_id}/decision", headers=_headers(api_dataset, source)
    )
    assert decision.status_code == 200
    body = decision.json()
    assert {
        "load",
        "as_of",
        "ranking_model_version",
        "pricing_model_version",
        "pricing",
        "confidence",
        "ranked_carriers",
        "comparable_loads",
        "warnings",
    } <= set(body)
    assert body["pricing"]["currency"] == "USD"
    assert body["load"]["load_number"] == "BO-9001"
    assert all(
        stop["latitude"] is not None
        and stop["longitude"] is not None
        and isinstance(stop["geography_quality_flags"], list)
        for stop in body["load"]["stops"]
    )
    assert re.fullmatch(r"\d+(?:\.\d+)?", body["pricing"]["point_estimate_usd"])
    assert body["ranked_carriers"]
    assert all(
        item["evidence_ids"] and item["explanation_bullets"] for item in body["ranked_carriers"]
    )
    evidence_summaries = [
        summary
        for ranked in body["ranked_carriers"]
        for summaries in ranked["evidence_by_component"].values()
        for summary in summaries
    ]
    assert evidence_summaries
    assert all(
        summary["load_external_id"] and summary["completed_observed_at"] and "→" in summary["route"]
        for summary in evidence_summaries
    )
    lane_summaries = [
        summary
        for ranked in body["ranked_carriers"]
        for summary in ranked["evidence_by_component"].get("lane", [])
    ]
    assert lane_summaries
    assert all(
        summary["origin_distance_miles"] is not None
        and summary["destination_distance_miles"] is not None
        for summary in lane_summaries
    )
    deadhead_summaries = [
        summary
        for ranked in body["ranked_carriers"]
        for summary in ranked["evidence_by_component"].get("deadhead", [])
    ]
    assert deadhead_summaries
    assert any(
        summary["delivery_to_pickup_miles"] is not None
        and summary["delivery_to_pickup_gap_days"] is not None
        for summary in deadhead_summaries
    )
    assert all(
        not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            summary["load_external_id"],
        )
        for summary in evidence_summaries
    )
    assert body["comparable_loads"]
    assert all(
        item["load_external_id"]
        and item["load_number"]
        and item["route"]
        and item["completed_observed_at"]
        and item["carrier_rate_usd"]
        for item in body["comparable_loads"]
    )

    schema = json.loads((Path(__file__).parents[2] / "frontend" / "openapi.json").read_text())
    required = schema["components"]["schemas"]["DecisionResponse"]["required"]
    assert set(required) <= set(body)


def test_cross_tenant_matches_absent_and_evidence_never_crosses_tenants(
    api_dataset: ApiDataset,
) -> None:
    client = TestClient(app)
    own_source, other_source = SourceSystem.FREIGHTFLOW, SourceSystem.HAULDESK
    headers = _headers(api_dataset, own_source)
    cross = client.get(f"/api/v1/loads/{api_dataset.day11_load_ids[other_source]}", headers=headers)
    missing = client.get(f"/api/v1/loads/{uuid4()}", headers=headers)
    assert cross.status_code == missing.status_code == 404
    assert cross.json() == missing.json() == {"detail": "Load not found."}

    decision = client.get(
        f"/api/v1/loads/{api_dataset.day11_load_ids[own_source]}/decision", headers=headers
    )
    assert decision.status_code == 200
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    try:
        with Session(engine) as session:
            set_tenant_context(session, api_dataset.tenant_ids[other_source])
            foreign_version_ids = {
                str(value)
                for value in session.scalars(
                    select(LoadVersion.id).where(
                        LoadVersion.tenant_id == api_dataset.tenant_ids[other_source]
                    )
                )
            }
            foreign_carrier_ids = {
                str(value)
                for value in session.scalars(
                    select(Carrier.id).where(
                        Carrier.tenant_id == api_dataset.tenant_ids[other_source]
                    )
                )
            }
    finally:
        engine.dispose()
    body = decision.json()
    evidence_ids = {value for ranked in body["ranked_carriers"] for value in ranked["evidence_ids"]}
    assert not evidence_ids & foreign_version_ids
    ranked_carrier_ids = {ranked["carrier_id"] for ranked in body["ranked_carriers"]}
    assert not ranked_carrier_ids & foreign_carrier_ids
    assert all(
        "load_version_id" not in item and "load_id" not in item for item in body["comparable_loads"]
    )


def test_broker_a_history_never_changes_broker_b_prediction(api_dataset: ApiDataset) -> None:
    """Tenant A's new shared-authority history cannot affect Tenant B's answer."""
    assert DATABASE_URL is not None
    tenant_a = api_dataset.tenant_ids[SourceSystem.FREIGHTFLOW]
    tenant_b = api_dataset.tenant_ids[SourceSystem.HAULDESK]
    tenant_b_load_id = api_dataset.day11_load_ids[SourceSystem.HAULDESK]
    engine = create_engine(DATABASE_URL)
    try:
        with Session(engine) as session:
            before = _serialized_prediction(session, tenant_b, tenant_b_load_id)
            session.commit()
            with session.begin():
                _insert_additional_freightflow_history(session, tenant_a)
            after = _serialized_prediction(session, tenant_b, tenant_b_load_id)
            assert after == before
    finally:
        engine.dispose()


def test_foreign_carrier_reference_is_hidden_as_generic_not_found(api_dataset: ApiDataset) -> None:
    """A malformed cross-tenant carrier reference cannot reveal that carrier via the API."""
    assert DATABASE_URL is not None
    own_source, other_source = SourceSystem.FREIGHTFLOW, SourceSystem.HAULDESK
    own_tenant = api_dataset.tenant_ids[own_source]
    engine = create_engine(DATABASE_URL)
    try:
        with Session(engine) as session, session.begin():
            set_tenant_context(session, api_dataset.tenant_ids[other_source])
            foreign_carrier = session.scalar(
                select(Carrier).where(Carrier.tenant_id == api_dataset.tenant_ids[other_source])
            )
            assert foreign_carrier is not None
            set_tenant_context(session, own_tenant)
            load = session.scalar(
                select(Load).where(
                    Load.tenant_id == own_tenant,
                    Load.id == api_dataset.day11_load_ids[own_source],
                )
            )
            assert load is not None and load.current_version_id is not None
            load_id = load.id
            decision = DecisionRun(
                tenant_id=own_tenant,
                load_id=load.id,
                input_version_id=load.current_version_id,
                as_of=load.observed_at,
                ranking_model_version="security-test",
                pricing_model_version="security-test",
                model_parameters={},
                price_estimate={"point_estimate_usd": "1200.00"},
                confidence={"level": "LOW", "score": "0.2", "components": {}},
                evidence_summary={},
            )
            session.add(decision)
            session.flush()
            session.add(
                CarrierRecommendation(
                    tenant_id=own_tenant,
                    decision_run_id=decision.id,
                    carrier_id=foreign_carrier.id,
                    rank=1,
                    raw_score="50",
                    adjusted_score="50",
                    confidence="0.2",
                    component_values={},
                    explanation_reason_codes=[],
                    evidence_ids=[],
                )
            )

        client = TestClient(app)
        headers = _headers(api_dataset, own_source)
        cross = client.get(f"/api/v1/loads/{load_id}/decision", headers=headers)
        missing = client.get(f"/api/v1/loads/{uuid4()}/decision", headers=headers)
        assert cross.status_code == missing.status_code == 404
        assert cross.json() == missing.json() == {"detail": "Load not found."}
    finally:
        engine.dispose()


def test_same_mc_dot_carriers_remain_separate_tenant_records(api_dataset: ApiDataset) -> None:
    """Matching public authority identifiers never merge mandatory tenant carriers."""
    assert DATABASE_URL is not None
    freightflow = api_dataset.tenant_ids[SourceSystem.FREIGHTFLOW]
    hauldesk = api_dataset.tenant_ids[SourceSystem.HAULDESK]
    engine = create_engine(DATABASE_URL)
    try:
        with Session(engine) as session, session.begin():
            freightflow_carrier = _insert_shared_authority_carrier(
                session, freightflow, SourceSystem.FREIGHTFLOW
            )
            hauldesk_carrier = _insert_shared_authority_carrier(
                session, hauldesk, SourceSystem.HAULDESK
            )
            assert freightflow_carrier.id != hauldesk_carrier.id
            assert freightflow_carrier.external_id != hauldesk_carrier.external_id
    finally:
        engine.dispose()


def test_decision_response_drops_embedded_foreign_evidence(api_dataset: ApiDataset) -> None:
    """JSONB evidence has no FK, so the API must re-authorize it before display."""
    assert DATABASE_URL is not None
    own_source, other_source = SourceSystem.FREIGHTFLOW, SourceSystem.HAULDESK
    engine = create_engine(DATABASE_URL)
    try:
        with Session(engine) as session, session.begin():
            set_tenant_context(session, api_dataset.tenant_ids[other_source])
            foreign_version = session.scalar(
                select(LoadVersion).where(
                    LoadVersion.tenant_id == api_dataset.tenant_ids[other_source]
                )
            )
            assert foreign_version is not None

            set_tenant_context(session, api_dataset.tenant_ids[own_source])
            load = session.scalar(
                select(Load).where(
                    Load.tenant_id == api_dataset.tenant_ids[own_source],
                    Load.id == api_dataset.day11_load_ids[own_source],
                )
            )
            assert load is not None and load.current_version_id is not None
            carrier = session.scalar(
                select(Carrier).where(
                    Carrier.tenant_id == api_dataset.tenant_ids[own_source],
                )
            )
            assert carrier is not None
            carrier_id = str(carrier.id)
            decision = DecisionRun(
                tenant_id=load.tenant_id,
                load_id=load.id,
                input_version_id=load.current_version_id,
                as_of=load.observed_at,
                ranking_model_version="security-test",
                pricing_model_version="security-test",
                model_parameters={},
                price_estimate={
                    "currency": "USD",
                    "point_estimate_usd": "1200.00",
                    "historical_comparison_lower_usd": "1100.00",
                    "historical_comparison_upper_usd": "1300.00",
                    "local_tier": "NEAR_EXACT",
                    "broader_tier": None,
                    "blend_local_weight": "1",
                    "raw_evidence_count": 1,
                    "effective_evidence_count": "1",
                    "warnings": [],
                },
                confidence={"level": "LOW", "score": "0.2", "components": {}},
                evidence_summary={
                    "comparable_loads": [
                        {
                            "load_version_id": str(foreign_version.id),
                            "load_external_id": "FOREIGN-LOAD",
                            "route": "Foreign origin → Foreign destination",
                            "carrier_rate_usd": "9999.00",
                        }
                    ],
                    "ranking_evidence": {
                        carrier.external_id: {
                            "components": {
                                "lane": [
                                    {
                                        "load_version_id": str(foreign_version.id),
                                        "load_external_id": "FOREIGN-LOAD",
                                        "route": "Foreign origin → Foreign destination",
                                    }
                                ]
                            }
                        }
                    },
                },
            )
            session.add(decision)
            session.flush()
            session.add(
                CarrierRecommendation(
                    tenant_id=load.tenant_id,
                    decision_run_id=decision.id,
                    carrier_id=carrier.id,
                    rank=1,
                    raw_score="50",
                    adjusted_score="50",
                    confidence="0.2",
                    component_values={},
                    explanation_reason_codes=[],
                    evidence_ids=[str(foreign_version.id)],
                )
            )

        response = TestClient(app).get(
            f"/api/v1/loads/{api_dataset.day11_load_ids[own_source]}/decision",
            headers=_headers(api_dataset, own_source),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["comparable_loads"] == []
        ranked = next(item for item in body["ranked_carriers"] if item["carrier_id"] == carrier_id)
        assert ranked["evidence_ids"] == []
        assert ranked["evidence_by_component"]["lane"] == []
        assert "FOREIGN-LOAD" not in response.text
    finally:
        engine.dispose()


def test_inactive_and_insufficient_decision_responses_are_stable(api_dataset: ApiDataset) -> None:
    assert DATABASE_URL is not None
    source = SourceSystem.FREIGHTFLOW
    engine = create_engine(DATABASE_URL)
    try:
        with Session(engine) as session:
            set_tenant_context(session, api_dataset.tenant_ids[source])
            inactive = session.scalar(
                select(Load).where(
                    Load.tenant_id == api_dataset.tenant_ids[source],
                    Load.status != LoadStatus.ACTIVE,
                )
            )
            assert inactive is not None
            active = session.scalar(
                select(Load).where(Load.id == api_dataset.day11_load_ids[source])
            )
            assert active is not None and active.current_version_id is not None
            session.add(
                DecisionRun(
                    tenant_id=active.tenant_id,
                    load_id=active.id,
                    input_version_id=active.current_version_id,
                    as_of=active.observed_at,
                    ranking_model_version="test-ranking",
                    pricing_model_version="test-pricing",
                    model_parameters={},
                    price_estimate={"point_estimate_usd": None},
                    confidence={"level": "LOW", "score": "0", "components": {}},
                    evidence_summary={},
                )
            )
            low_source = SourceSystem.BROKEROS
            set_tenant_context(session, api_dataset.tenant_ids[low_source])
            low_load = session.scalar(
                select(Load).where(Load.id == api_dataset.day11_load_ids[low_source])
            )
            low_carrier = session.scalar(
                select(Carrier).where(Carrier.tenant_id == api_dataset.tenant_ids[low_source])
            )
            assert low_load is not None and low_load.current_version_id is not None
            assert low_carrier is not None
            low_decision = DecisionRun(
                tenant_id=low_load.tenant_id,
                load_id=low_load.id,
                input_version_id=low_load.current_version_id,
                as_of=low_load.observed_at,
                ranking_model_version="test-ranking-low",
                pricing_model_version="test-pricing-low",
                model_parameters={},
                price_estimate={
                    "point_estimate_usd": "1200.00",
                    "historical_comparison_lower_usd": "1100.00",
                    "historical_comparison_upper_usd": "1300.00",
                    "local_tier": "TENANT_ALL_EQUIPMENT",
                    "broader_tier": None,
                    "blend_local_weight": "1",
                    "raw_evidence_count": 1,
                    "effective_evidence_count": "1",
                    "warnings": ["SPARSE_EVIDENCE"],
                },
                confidence={"level": "LOW", "score": "0.2", "components": {}},
                evidence_summary={"comparable_loads": []},
            )
            session.add(low_decision)
            session.flush()
            session.add(
                CarrierRecommendation(
                    tenant_id=low_load.tenant_id,
                    decision_run_id=low_decision.id,
                    carrier_id=low_carrier.id,
                    rank=1,
                    raw_score="50",
                    adjusted_score="50",
                    confidence="0.2",
                    component_values={},
                    explanation_reason_codes=["SPARSE_HISTORY_SHRINKAGE"],
                    evidence_ids=[],
                )
            )
            session.commit()
            inactive_id = inactive.id
            active_id = active.id
            low_load_id = low_load.id
        client = TestClient(app)
        headers = _headers(api_dataset, source)
        assert client.get(f"/api/v1/loads/{inactive_id}/decision", headers=headers).json() == {
            "detail": "Load is not active."
        }
        assert client.get(f"/api/v1/loads/{active_id}/decision", headers=headers).json() == {
            "detail": "Insufficient decision evidence."
        }
        low = client.get(
            f"/api/v1/loads/{low_load_id}/decision", headers=_headers(api_dataset, low_source)
        )
        assert low.status_code == 200
        assert low.json()["pricing"]["currency"] == "USD"
        assert low.json()["confidence"]["level"] == "LOW"
    finally:
        engine.dispose()
