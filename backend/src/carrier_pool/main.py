"""Tenant-scoped FastAPI transport for persisted Carrier Pool decisions."""

import os
from collections.abc import Generator
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Carrier, CarrierRecommendation, DecisionRun, Load, Stop, Tenant
from carrier_pool.db.tenant import set_tenant_context
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


class LoadResponse(BaseModel):
    id: str
    external_id: str
    status: str
    equipment: str | None
    distance_miles: str | None
    observed_at: str
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


class RankedCarrierResponse(BaseModel):
    rank: int
    carrier_id: str
    carrier_name: str
    adjusted_score: str
    confidence_score: str
    component_scores: dict[str, str]
    reason_codes: list[str]
    explanation_bullets: list[str]
    evidence_ids: list[str]


class DecisionResponse(BaseModel):
    load: LoadResponse
    as_of: str
    ranking_model_version: str
    pricing_model_version: str
    model_parameters: dict[str, Any]
    pricing: PricingResponse
    confidence: ConfidenceResponse
    ranked_carriers: list[RankedCarrierResponse]
    comparable_loads: list[dict[str, Any]]
    warnings: list[str]


app = FastAPI(title="Carrier Pool API", version="0.1.0")


def database_session() -> Generator[Session]:
    """Open one app-role database session for a request."""
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        yield session


def tenant_context(x_tenant_id: str | None = Header(default=None)) -> UUID:
    """Accept only explicit UUID demo tenant context."""
    try:
        return UUID(x_tenant_id) if x_tenant_id is not None else (_ for _ in ()).throw(ValueError)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid tenant context.") from error


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
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
        for tenant in session.scalars(select(Tenant).order_by(Tenant.name)).all()
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
    pricing = decision.price_estimate
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
    return DecisionResponse(
        load=_load_response(session, tenant_id, load),
        as_of=decision.as_of.isoformat(),
        ranking_model_version=decision.ranking_model_version,
        pricing_model_version=decision.pricing_model_version,
        model_parameters=decision.model_parameters,
        pricing=PricingResponse(**pricing),
        confidence=ConfidenceResponse(**decision.confidence),
        ranked_carriers=[
            RankedCarrierResponse(
                rank=item.rank,
                carrier_id=str(item.carrier_id),
                carrier_name=carriers[item.carrier_id].name,
                adjusted_score=str(item.adjusted_score),
                confidence_score=str(item.confidence),
                component_scores={key: str(value) for key, value in item.component_values.items()},
                reason_codes=item.explanation_reason_codes,
                explanation_bullets=[
                    _reason_bullet(code) for code in item.explanation_reason_codes
                ],
                evidence_ids=item.evidence_ids,
            )
            for item in recommendations
        ],
        comparable_loads=list(decision.evidence_summary.get("comparable_loads", [])),
        warnings=warnings,
    )


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
    return LoadResponse(
        id=str(load.id),
        external_id=load.external_id,
        status=load.status.value,
        equipment=None if load.equipment is None else load.equipment.value,
        distance_miles=None if load.distance_miles is None else str(load.distance_miles),
        observed_at=load.observed_at.isoformat(),
        stops=[
            StopResponse(
                sequence=stop.sequence,
                is_pickup=stop.is_pickup,
                is_dropoff=stop.is_dropoff,
                city=stop.city,
                state=stop.state,
                postal_code=stop.postal_code,
            )
            for stop in stops
        ],
    )


def _reason_bullet(code: str) -> str:
    return {
        "DIRECTIONAL_LANE_HISTORY": "Completed directional historical loads support this fit.",
        "EQUIPMENT_HISTORY": "Completed equipment-matching loads are recorded.",
        "HISTORICAL_DELIVERY_PROXIMITY": "Historical delivery evidence is not live location.",
        "SPARSE_HISTORY_SHRINKAGE": "Limited completed history pulls score toward neutral prior.",
        "UNKNOWN_TARGET_EQUIPMENT": "Target equipment is unknown, so confidence is limited.",
    }.get(code, "Historical-fit evidence recorded.")
