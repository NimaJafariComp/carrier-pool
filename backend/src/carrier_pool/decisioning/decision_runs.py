"""Immutable, tenant-scoped persistence for reproducible decision outputs."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Carrier, CarrierRecommendation, DecisionRun, LoadVersion
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.carrier_explanations import explain_rankings
from carrier_pool.decisioning.carrier_features import CarrierFeatureService, CarrierFeatureSet
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFitScorer
from carrier_pool.decisioning.pricing import HierarchicalRateEstimator, RateEstimate
from carrier_pool.domain.types import LoadStatus

IDENTITY_RULE = (
    "tenant_id, load_id, exact input_version_id, as_of, ranking model version, "
    "pricing model version, and model parameters"
)


@dataclass(frozen=True, slots=True)
class PersistedDecision:
    """One immutable run and its ordered immutable recommendation rows."""

    run: DecisionRun
    recommendations: tuple[CarrierRecommendation, ...]
    reused: bool


def decision_identity(
    tenant_id: str,
    load_id: str,
    input_version_id: str,
    as_of: str,
    ranking_model_version: str,
    pricing_model_version: str,
    model_parameters: dict[str, Any] | None = None,
) -> str:
    """Hash exact persisted-decision reuse inputs; outputs never determine identity."""
    payload = {
        "tenant_id": tenant_id,
        "load_id": load_id,
        "input_version_id": input_version_id,
        "as_of": as_of,
        "ranking_model_version": ranking_model_version,
        "pricing_model_version": pricing_model_version,
        "model_parameters": model_parameters or {},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class DecisionRunService:
    """Compute and persist only immutable as-of decision inputs and outputs."""

    def __init__(
        self,
        estimator: HierarchicalRateEstimator | None = None,
        features: CarrierFeatureService | None = None,
        scorer: CarrierHistoricalFitScorer | None = None,
    ) -> None:
        self._estimator = estimator or HierarchicalRateEstimator()
        self._features = features or CarrierFeatureService()
        self._scorer = scorer or CarrierHistoricalFitScorer()

    def run(
        self, session: Session, tenant_id: UUID, load_id: UUID, as_of: datetime
    ) -> PersistedDecision:
        """Create or reuse exact-identity decision for an ACTIVE immutable version."""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        input_version = self._active_input_version(session, tenant_id, load_id, as_of)
        estimate = self._estimator.estimate(session, tenant_id, load_id, as_of)
        features = self._features.retrieve(session, tenant_id, load_id, input_version.id, as_of)
        rankings = self._scorer.score(features)
        explanations = explain_rankings(rankings, features)
        parameters = {
            "identity_rule": IDENTITY_RULE,
            "ranking": {
                "component_weights": {
                    "lane": "0.4",
                    "equipment": "0.2",
                    "deadhead": "0.2",
                    "recency": "0.2",
                },
                "neutral_prior": "50",
                "shrinkage_k": "6",
            },
            "pricing": {"shrinkage_strength": "6", "sparse_ess_threshold": "4"},
        }
        existing = self._existing(
            session,
            tenant_id,
            load_id,
            input_version.id,
            as_of,
            rankings[0].model_version if rankings else "carrier-ranking-v1",
            estimate.model_version,
            parameters,
        )
        if existing is not None:
            return PersistedDecision(
                existing, self._recommendations(session, tenant_id, existing.id), True
            )
        run = DecisionRun(
            tenant_id=tenant_id,
            load_id=load_id,
            input_version_id=input_version.id,
            as_of=as_of,
            ranking_model_version=rankings[0].model_version if rankings else "carrier-ranking-v1",
            pricing_model_version=estimate.model_version,
            model_parameters=parameters,
            price_estimate=_price_json(estimate),
            confidence={
                "level": estimate.confidence.level.value,
                "score": str(estimate.confidence.score),
                "components": _decimal_map(estimate.confidence.components),
            },
            evidence_summary={
                "pricing_evidence_ids": [
                    str(item.load_version_id) for item in estimate.comparables
                ],
                "comparable_loads": [
                    {
                        "load_id": str(item.load_id),
                        "load_external_id": item.load_external_id,
                        "load_version_id": str(item.load_version_id),
                        "carrier_rate_usd": str(item.carrier_rate_usd),
                        "tier": item.tier.value,
                        "origin_distance_miles": item.origin_distance_miles,
                        "destination_distance_miles": item.destination_distance_miles,
                        "route_mile_difference": None
                        if item.route_mile_difference is None
                        else str(item.route_mile_difference),
                        "recency_days": item.recency_days,
                        "weight": str(item.weight),
                        "evidence_ids": list(item.evidence_ids),
                    }
                    for item in estimate.comparables
                ],
                "pricing_warnings": list(estimate.warnings),
                "ranking_identity": decision_identity(
                    str(tenant_id),
                    str(load_id),
                    str(input_version.id),
                    as_of.isoformat(),
                    rankings[0].model_version if rankings else "carrier-ranking-v1",
                    estimate.model_version,
                    parameters,
                ),
            },
        )
        session.add(run)
        session.flush()
        carrier_ids = {
            carrier.external_id: carrier.id
            for carrier in session.scalars(
                select(Carrier).where(Carrier.tenant_id == tenant_id)
            ).all()
        }
        by_fit = {item.carrier_external_id: item for item in rankings}
        by_feature = {item.carrier_external_id: item for item in features}
        rows: list[CarrierRecommendation] = []
        for explanation in explanations:
            fit = by_fit[explanation.carrier_external_id]
            carrier_id = carrier_ids.get(explanation.carrier_external_id)
            if carrier_id is None:
                raise LookupError("ranked carrier not found")
            row = CarrierRecommendation(
                tenant_id=tenant_id,
                decision_run_id=run.id,
                carrier_id=carrier_id,
                rank=explanation.rank,
                raw_score=fit.raw_score,
                adjusted_score=fit.adjusted_score,
                confidence=fit.confidence_score,
                component_values=_decimal_map(explanation.component_scores),
                explanation_reason_codes=_reason_codes(
                    explanation.warnings, by_feature[explanation.carrier_external_id]
                ),
                evidence_ids=list(explanation.supporting_load_ids),
            )
            session.add(row)
            rows.append(row)
        session.flush()
        return PersistedDecision(run, tuple(rows), False)

    @staticmethod
    def _active_input_version(
        session: Session, tenant_id: UUID, load_id: UUID, as_of: datetime
    ) -> LoadVersion:
        set_tenant_context(session, tenant_id)
        version = session.scalar(
            select(LoadVersion)
            .where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.load_id == load_id,
                LoadVersion.observed_at <= as_of,
            )
            .order_by(LoadVersion.observed_at.desc(), LoadVersion.id.desc())
        )
        if version is None:
            raise LookupError("load not found at as_of")
        if version.status is not LoadStatus.ACTIVE:
            raise ValueError("decision input must be ACTIVE at as_of")
        return version

    @staticmethod
    def _existing(
        session: Session,
        tenant_id: UUID,
        load_id: UUID,
        input_version_id: UUID,
        as_of: datetime,
        ranking_model_version: str,
        pricing_model_version: str,
        parameters: dict[str, Any],
    ) -> DecisionRun | None:
        rows = session.scalars(
            select(DecisionRun)
            .where(
                DecisionRun.tenant_id == tenant_id,
                DecisionRun.load_id == load_id,
                DecisionRun.input_version_id == input_version_id,
                DecisionRun.as_of == as_of,
                DecisionRun.ranking_model_version == ranking_model_version,
                DecisionRun.pricing_model_version == pricing_model_version,
            )
            .order_by(DecisionRun.created_at)
        ).all()
        return next((item for item in rows if item.model_parameters == parameters), None)

    @staticmethod
    def _recommendations(
        session: Session, tenant_id: UUID, decision_run_id: UUID
    ) -> tuple[CarrierRecommendation, ...]:
        return tuple(
            session.scalars(
                select(CarrierRecommendation)
                .where(
                    CarrierRecommendation.tenant_id == tenant_id,
                    CarrierRecommendation.decision_run_id == decision_run_id,
                )
                .order_by(CarrierRecommendation.rank)
            ).all()
        )


def _decimal_map(values: dict[str, Decimal]) -> dict[str, str]:
    return {key: str(value) for key, value in values.items()}


def _price_json(estimate: RateEstimate) -> dict[str, Any]:
    return {
        "currency": "USD",
        "point_estimate_usd": None
        if estimate.point_estimate_usd is None
        else str(estimate.point_estimate_usd),
        "historical_comparison_lower_usd": None
        if estimate.historical_comparison_lower_usd is None
        else str(estimate.historical_comparison_lower_usd),
        "historical_comparison_upper_usd": None
        if estimate.historical_comparison_upper_usd is None
        else str(estimate.historical_comparison_upper_usd),
        "local_tier": None if estimate.local_tier is None else estimate.local_tier.value,
        "broader_tier": None if estimate.broader_tier is None else estimate.broader_tier.value,
        "blend_local_weight": None
        if estimate.blend_local_weight is None
        else str(estimate.blend_local_weight),
        "raw_evidence_count": estimate.raw_evidence_count,
        "effective_evidence_count": str(estimate.effective_evidence_count),
        "warnings": list(estimate.warnings),
    }


def _reason_codes(warnings: tuple[str, ...], feature: CarrierFeatureSet) -> list[str]:
    """Persist deterministic, structured historical-fit reasons, never claims."""
    codes: list[str] = []
    if feature.lane_history:
        codes.append("DIRECTIONAL_LANE_HISTORY")
    if feature.equipment_history_count:
        codes.append("EQUIPMENT_HISTORY")
    if feature.delivery_to_pickup_miles is not None:
        codes.append("HISTORICAL_DELIVERY_PROXIMITY")
    return [*codes, *warnings]
