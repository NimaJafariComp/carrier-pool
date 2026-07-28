"""Tenant-scoped FastAPI transport for persisted Carrier Pool decisions."""

import os
from collections.abc import Generator, Iterable, Sequence
from typing import Any, cast
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    Carrier,
    CarrierRecommendation,
    DecisionRun,
    Load,
    LoadVersion,
    Stop,
    Tenant,
)
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.demo import DEMO_TENANT_IDS, DEMO_TENANT_SLUGS
from carrier_pool.domain.types import LoadStatus


class HealthResponse(BaseModel):
    status: str


class TenantResponse(BaseModel):
    id: str
    slug: str
    name: str
    source_system: str


class StopResponse(BaseModel):
    sequence: int
    is_pickup: bool
    is_dropoff: bool
    city: str
    state: str
    postal_code: str
    planned_date: str | None
    scheduled_start_at: str | None


class LoadResponse(BaseModel):
    id: str
    external_id: str
    status: str
    equipment: str | None
    distance_miles: str | None
    observed_at: str
    expected_rate_usd: str | None
    confidence: str | None
    stops: list[StopResponse]


class PricingResponse(BaseModel):
    currency: str
    point_estimate_usd: str | None
    historical_comparison_lower_usd: str | None
    historical_comparison_upper_usd: str | None
    local_tier: str | None
    broader_tier: str | None
    blend_local_weight: str | None
    raw_evidence_count: int
    effective_evidence_count: str
    warnings: list[str]


class ConfidenceResponse(BaseModel):
    level: str
    score: str
    components: dict[str, str]


class EvidenceLoadResponse(BaseModel):
    """A human-readable, tenant-local completed-load reference."""

    load_external_id: str
    route: str
    equipment: str | None = None
    completed_observed_at: str | None = None
    distance_miles: str | None = None
    tier: str | None = None
    origin_distance_miles: float | None = None
    destination_distance_miles: float | None = None


class RankedCarrierResponse(BaseModel):
    rank: int
    carrier_id: str
    carrier_name: str
    adjusted_score: str
    confidence_score: str
    component_scores: dict[str, str | None]
    reason_codes: list[str]
    explanation_bullets: list[str]
    evidence_ids: list[str]
    evidence_status: str
    tie_group: int | None
    evidence_by_component: dict[str, list[EvidenceLoadResponse]]


class ComparableLoadResponse(EvidenceLoadResponse):
    """Safe, readable rate-comparison evidence for one tenant-owned load."""

    carrier_rate_usd: str | None = None
    route_mile_difference: str | None = None
    recency_days: float | None = None


class DecisionResponse(BaseModel):
    load: LoadResponse
    as_of: str
    ranking_model_version: str
    pricing_model_version: str
    model_parameters: dict[str, Any]
    pricing: PricingResponse
    confidence: ConfidenceResponse
    ranked_carriers: list[RankedCarrierResponse]
    comparable_loads: list[ComparableLoadResponse]
    warnings: list[str]


app = FastAPI(title="Carrier Pool API", version="0.1.0")


def database_session() -> Generator[Session]:
    """Open one app-role database session for a request."""
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        yield session


def tenant_context(x_tenant_id: str | None = Header(default=None)) -> UUID:
    """Accept only one server-authored demo broker binding."""
    try:
        tenant_id = (
            UUID(x_tenant_id) if x_tenant_id is not None else (_ for _ in ()).throw(ValueError)
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid tenant context.") from error
    if tenant_id not in DEMO_TENANT_IDS:
        raise HTTPException(status_code=400, detail="Invalid tenant context.")
    return tenant_id


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/ready", response_model=HealthResponse, tags=["health"])
def ready() -> HealthResponse:
    """Report readiness only when the app-role database connection succeeds."""
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Service unavailable.") from error
    finally:
        engine.dispose()
    return HealthResponse(status="ok")


@app.get("/api/v1/tenants", response_model=list[TenantResponse], tags=["tenants"])
def list_tenants(session: Session = Depends(database_session)) -> list[TenantResponse]:  # noqa: B008
    """Return safe public fictional-broker fields for demo selection."""
    return [
        TenantResponse(
            id=str(tenant.id),
            slug=tenant.slug,
            name=tenant.name,
            source_system=tenant.source_system.value,
        )
        for tenant in session.scalars(
            select(Tenant).where(Tenant.slug.in_(DEMO_TENANT_SLUGS)).order_by(Tenant.name)
        ).all()
    ]


@app.get("/api/v1/loads", response_model=list[LoadResponse], tags=["loads"])
def list_loads(
    status: LoadStatus = Query(default=LoadStatus.ACTIVE),  # noqa: B008
    tenant_id: UUID = Depends(tenant_context),  # noqa: B008
    session: Session = Depends(database_session),  # noqa: B008
) -> list[LoadResponse]:
    """List only caller-tenant current load projections, ACTIVE by default."""
    set_tenant_context(session, tenant_id)
    loads = session.scalars(
        select(Load)
        .where(Load.tenant_id == tenant_id, Load.status == status)
        .order_by(Load.observed_at.desc(), Load.id)
    ).all()
    return [_load_response(session, tenant_id, load) for load in loads]


@app.get("/api/v1/loads/{load_id}", response_model=LoadResponse, tags=["loads"])
def get_load(
    load_id: UUID,
    tenant_id: UUID = Depends(tenant_context),  # noqa: B008
    session: Session = Depends(database_session),  # noqa: B008
) -> LoadResponse:
    """Return current tenant-owned load; hide cross-tenant existence."""
    load = _load_or_not_found(session, tenant_id, load_id)
    return _load_response(session, tenant_id, load)


@app.get("/api/v1/loads/{load_id}/decision", response_model=DecisionResponse, tags=["decisions"])
def get_decision(
    load_id: UUID,
    tenant_id: UUID = Depends(tenant_context),  # noqa: B008
    session: Session = Depends(database_session),  # noqa: B008
) -> DecisionResponse:
    """Return latest persisted decision for current ACTIVE input, never recompute implicitly."""
    load = _load_or_not_found(session, tenant_id, load_id)
    if load.status is not LoadStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Load is not active.")
    decision = session.scalar(
        select(DecisionRun)
        .where(
            DecisionRun.tenant_id == tenant_id,
            DecisionRun.load_id == load.id,
            DecisionRun.input_version_id == load.current_version_id,
        )
        .order_by(DecisionRun.created_at.desc())
    )
    if decision is None:
        raise HTTPException(status_code=409, detail="Decision not computed.")
    pricing = dict(decision.price_estimate)
    pricing.setdefault("currency", "USD")
    if pricing.get("point_estimate_usd") is None:
        raise HTTPException(status_code=422, detail="Insufficient decision evidence.")
    recommendations = session.scalars(
        select(CarrierRecommendation)
        .where(
            CarrierRecommendation.tenant_id == tenant_id,
            CarrierRecommendation.decision_run_id == decision.id,
        )
        .order_by(CarrierRecommendation.rank)
    ).all()
    if not recommendations:
        raise HTTPException(status_code=422, detail="Insufficient decision evidence.")
    carriers = {
        carrier.id: carrier
        for carrier in session.scalars(
            select(Carrier).where(
                Carrier.tenant_id == tenant_id,
                Carrier.id.in_([item.carrier_id for item in recommendations]),
            )
        ).all()
    }
    if len(carriers) != len(recommendations):
        raise HTTPException(status_code=404, detail="Load not found.")
    warnings = list(pricing.get("warnings", []))
    ranking_evidence = decision.evidence_summary.get("ranking_evidence", {})
    evidence_version_ids = _tenant_evidence_version_ids(
        session, tenant_id, decision, recommendations
    )
    return DecisionResponse(
        load=_load_response(session, tenant_id, load),
        as_of=decision.as_of.isoformat(),
        ranking_model_version=decision.ranking_model_version,
        pricing_model_version=decision.pricing_model_version,
        model_parameters=decision.model_parameters,
        pricing=PricingResponse(**pricing),
        confidence=ConfidenceResponse(**decision.confidence),
        ranked_carriers=[
            _ranked_carrier_response(
                item,
                carriers[item.carrier_id],
                ranking_evidence,
                evidence_version_ids,
            )
            for item in recommendations
        ],
        comparable_loads=[
            _comparable_load_response(cast(dict[str, object], entry))
            for entry in cast(list[object], decision.evidence_summary.get("comparable_loads", []))
            if isinstance(entry, dict)
            and _has_tenant_evidence(cast(dict[str, object], entry), evidence_version_ids)
        ],
        warnings=warnings,
    )


def _ranked_carrier_response(
    recommendation: CarrierRecommendation,
    carrier: Carrier,
    ranking_evidence: object,
    evidence_version_ids: set[str],
) -> RankedCarrierResponse:
    raw_evidence = (
        cast(dict[str, Any], ranking_evidence) if isinstance(ranking_evidence, dict) else {}
    )
    stored_value = raw_evidence.get(carrier.external_id, {})
    stored = cast(dict[str, Any], stored_value) if isinstance(stored_value, dict) else {}
    components_value = stored.get("components", {})
    components = (
        cast(dict[str, Any], components_value) if isinstance(components_value, dict) else {}
    )
    bullets_value = stored.get("bullets", [])
    bullets = cast(list[object], bullets_value) if isinstance(bullets_value, list) else []
    status = stored.get("status")
    tie_group = stored.get("tie_group")
    return RankedCarrierResponse(
        rank=recommendation.rank,
        carrier_id=str(recommendation.carrier_id),
        carrier_name=carrier.name,
        adjusted_score=str(recommendation.adjusted_score),
        confidence_score=str(recommendation.confidence),
        component_scores={
            key: None if value is None else str(value)
            for key, value in recommendation.component_values.items()
        },
        reason_codes=recommendation.explanation_reason_codes,
        explanation_bullets=(
            [item for item in bullets if isinstance(item, str)]
            or [_reason_bullet(code) for code in recommendation.explanation_reason_codes]
        ),
        evidence_ids=[
            evidence_id
            for evidence_id in recommendation.evidence_ids
            if evidence_id in evidence_version_ids
        ],
        evidence_status=status if isinstance(status, str) else "SUPPORTED",
        tie_group=tie_group if isinstance(tie_group, int) else None,
        evidence_by_component={
            key: [
                _evidence_load_response(cast(dict[str, object], entry))
                for entry in cast(list[object], values)
                if isinstance(entry, dict)
                and _has_tenant_evidence(cast(dict[str, object], entry), evidence_version_ids)
            ]
            for key, values in components.items()
            if isinstance(values, list)
        },
    )


def _tenant_evidence_version_ids(
    session: Session,
    tenant_id: UUID,
    decision: DecisionRun,
    recommendations: Sequence[CarrierRecommendation],
) -> set[str]:
    """Authorize opaque stored evidence against tenant-local immutable versions.

    RLS protects the decision row itself, but JSONB fields are not relational
    foreign keys. Treat malformed, legacy, or cross-tenant embedded references as
    unavailable rather than serializing their descriptive fields.
    """
    candidates: set[UUID] = set()
    for recommendation in recommendations:
        candidates.update(_uuid_values(recommendation.evidence_ids))
    summary = cast(dict[str, object], decision.evidence_summary)
    comparable_loads = summary.get("comparable_loads", [])
    if isinstance(comparable_loads, list):
        for entry in cast(list[object], comparable_loads):
            if isinstance(entry, dict):
                typed_entry = cast(dict[str, object], entry)
                candidates.update(_uuid_values([typed_entry.get("load_version_id")]))
    ranking_evidence = summary.get("ranking_evidence", {})
    if isinstance(ranking_evidence, dict):
        typed_ranking_evidence = cast(dict[str, object], ranking_evidence)
        for carrier_evidence in typed_ranking_evidence.values():
            if not isinstance(carrier_evidence, dict):
                continue
            components = cast(dict[str, object], carrier_evidence).get("components", {})
            if not isinstance(components, dict):
                continue
            for entries in cast(dict[str, object], components).values():
                if isinstance(entries, list):
                    for entry in cast(list[object], entries):
                        if isinstance(entry, dict):
                            typed_entry = cast(dict[str, object], entry)
                            candidates.update(_uuid_values([typed_entry.get("load_version_id")]))
    if not candidates:
        return set()
    return {
        str(version_id)
        for version_id in session.scalars(
            select(LoadVersion.id).where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.id.in_(candidates),
            )
        ).all()
    }


def _uuid_values(values: Iterable[object]) -> set[UUID]:
    result: set[UUID] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            result.add(UUID(value))
        except ValueError:
            continue
    return result


def _has_tenant_evidence(entry: dict[str, object], evidence_version_ids: set[str]) -> bool:
    version_id = entry.get("load_version_id")
    return isinstance(version_id, str) and version_id in evidence_version_ids


def _evidence_load_response(entry: dict[str, object]) -> EvidenceLoadResponse:
    """Expose readable evidence, never a persisted database UUID as its label."""
    label = _string_or_none(entry.get("load_external_id")) or "Historical load"
    try:
        UUID(label)
    except ValueError:
        pass
    else:
        label = "Historical load"
    return EvidenceLoadResponse(
        load_external_id=label,
        route=_string_or_none(entry.get("route")) or "Route unavailable",
        equipment=_string_or_none(entry.get("equipment")),
        completed_observed_at=_string_or_none(entry.get("completed_observed_at")),
        distance_miles=_string_or_none(entry.get("distance_miles")),
        tier=_string_or_none(entry.get("tier")),
        origin_distance_miles=_float_or_none(entry.get("origin_distance_miles")),
        destination_distance_miles=_float_or_none(entry.get("destination_distance_miles")),
    )


def _comparable_load_response(entry: dict[str, object]) -> ComparableLoadResponse:
    """Strip database audit IDs while retaining display-safe comparison facts."""
    summary = _evidence_load_response(entry)
    return ComparableLoadResponse(
        **summary.model_dump(),
        carrier_rate_usd=_string_or_none(entry.get("carrier_rate_usd")),
        route_mile_difference=_string_or_none(entry.get("route_mile_difference")),
        recency_days=_float_or_none(entry.get("recency_days")),
    )


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _load_or_not_found(session: Session, tenant_id: UUID, load_id: UUID) -> Load:
    set_tenant_context(session, tenant_id)
    load = session.scalar(select(Load).where(Load.id == load_id, Load.tenant_id == tenant_id))
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found.")
    return load


def _load_response(session: Session, tenant_id: UUID, load: Load) -> LoadResponse:
    stops = session.scalars(
        select(Stop)
        .where(Stop.load_id == load.id, Stop.tenant_id == tenant_id)
        .order_by(Stop.sequence)
    ).all()
    planned_dates = _planned_dates(session, tenant_id, load.current_version_id)
    summary = _load_decision_summary(session, tenant_id, load)
    return LoadResponse(
        id=str(load.id),
        external_id=load.external_id,
        status=load.status.value,
        equipment=None if load.equipment is None else load.equipment.value,
        distance_miles=None if load.distance_miles is None else str(load.distance_miles),
        observed_at=load.observed_at.isoformat(),
        expected_rate_usd=summary[0],
        confidence=summary[1],
        stops=[
            StopResponse(
                sequence=stop.sequence,
                is_pickup=stop.is_pickup,
                is_dropoff=stop.is_dropoff,
                city=stop.city,
                state=stop.state,
                postal_code=stop.postal_code,
                planned_date=planned_dates.get(stop.sequence),
                scheduled_start_at=None
                if stop.scheduled_start_at is None
                else stop.scheduled_start_at.isoformat(),
            )
            for stop in stops
        ],
    )


def _planned_dates(
    session: Session, tenant_id: UUID, current_version_id: UUID | None
) -> dict[int, str]:
    """Expose source date-only schedules without inventing a time of day."""
    if current_version_id is None:
        return {}
    version = session.scalar(
        select(LoadVersion).where(
            LoadVersion.tenant_id == tenant_id,
            LoadVersion.id == current_version_id,
        )
    )
    if version is None:
        return {}
    raw_stops = version.canonical_snapshot.get("stops")
    if not isinstance(raw_stops, list):
        return {}
    result: dict[int, str] = {}
    for raw_stop in cast(list[object], raw_stops):
        if not isinstance(raw_stop, dict):
            continue
        stop = cast(dict[str, object], raw_stop)
        sequence = stop.get("sequence")
        planned_date = stop.get("planned_date")
        if isinstance(sequence, int) and isinstance(planned_date, str):
            result[sequence] = planned_date
    return result


def _load_decision_summary(
    session: Session, tenant_id: UUID, load: Load
) -> tuple[str | None, str | None]:
    """Expose a persisted current-input pricing summary without recomputing it."""
    if load.current_version_id is None:
        return None, None
    decision = session.scalar(
        select(DecisionRun)
        .where(
            DecisionRun.tenant_id == tenant_id,
            DecisionRun.load_id == load.id,
            DecisionRun.input_version_id == load.current_version_id,
        )
        .order_by(DecisionRun.created_at.desc())
    )
    if decision is None:
        return None, None
    rate = decision.price_estimate.get("point_estimate_usd")
    confidence = decision.confidence.get("level")
    return (
        rate if isinstance(rate, str) else None,
        confidence if isinstance(confidence, str) else None,
    )


def _reason_bullet(code: str) -> str:
    return {
        "DIRECTIONAL_LANE_HISTORY": "Completed directional historical loads support this fit.",
        "EQUIPMENT_HISTORY": "Completed equipment-matching loads are recorded.",
        "HISTORICAL_DELIVERY_PROXIMITY": (
            "The last recorded delivery helps compare historical proximity; "
            "it is not live location."
        ),
        "SPARSE_HISTORY_SHRINKAGE": "Limited completed history pulls score toward neutral prior.",
        "UNKNOWN_TARGET_EQUIPMENT": "Target equipment is unknown, so confidence is limited.",
        "DEADHEAD_LOCATION_UNAVAILABLE": (
            "No historical delivery-to-pickup distance is available for this carrier."
        ),
    }.get(code, "Historical-fit evidence recorded.")
