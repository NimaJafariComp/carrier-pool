"""Immutable, tenant-scoped persistence for reproducible decision outputs."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Carrier, CarrierRecommendation, DecisionRun, LoadVersion
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.carrier_explanations import RankedCarrierExplanation, explain_rankings
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
        ranking_evidence = _ranking_evidence(session, tenant_id, features, explanations, as_of)
        comparable_loads = _pricing_evidence(session, tenant_id, estimate, as_of)
        parameters = {
            "identity_rule": IDENTITY_RULE,
            "evidence_schema_version": "2",
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
            rankings[0].model_version if rankings else "carrier-ranking-v4",
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
            ranking_model_version=rankings[0].model_version if rankings else "carrier-ranking-v4",
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
                "comparable_loads": comparable_loads,
                "pricing_warnings": list(estimate.warnings),
                "ranking_evidence": ranking_evidence,
                "ranking_identity": decision_identity(
                    str(tenant_id),
                    str(load_id),
                    str(input_version.id),
                    as_of.isoformat(),
                    rankings[0].model_version if rankings else "carrier-ranking-v4",
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


def _decimal_map(values: Mapping[str, Decimal | None]) -> dict[str, str | None]:
    return {key: None if value is None else str(value) for key, value in values.items()}


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


def _ranking_evidence(
    session: Session,
    tenant_id: UUID,
    features: tuple[CarrierFeatureSet, ...],
    explanations: tuple[RankedCarrierExplanation, ...],
    as_of: datetime,
) -> dict[str, dict[str, Any]]:
    """Persist readable, component-scoped tenant-local evidence with each decision."""
    by_carrier = {item.carrier_external_id: item for item in features}
    version_ids = {
        version_id
        for feature in features
        for version_id in (
            *(item.version_id for item in feature.lane_history),
            *feature.equipment_history_version_ids,
            *feature.relevant_completed_version_ids,
            *(
                (feature.last_delivery_load_version_id,)
                if feature.last_delivery_load_version_id
                else ()
            ),
        )
    }
    versions = {
        version.id: version
        for version in session.scalars(
            select(LoadVersion).where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.id.in_(version_ids),
                LoadVersion.observed_at <= as_of,
            )
        ).all()
    }
    result: dict[str, dict[str, Any]] = {}
    for explanation in explanations:
        feature = by_carrier[explanation.carrier_external_id]
        lane_tiers = {item.version_id: item.tier.value for item in feature.lane_history}
        result[explanation.carrier_external_id] = {
            "status": explanation.evidence_status,
            "tie_group": explanation.tie_group,
            "bullets": list(explanation.evidence_bullets),
            "components": {
                "lane": [
                    _evidence_summary(
                        versions[item.version_id],
                        tier=item.tier.value,
                        origin_distance_miles=item.origin_distance_miles,
                        destination_distance_miles=item.destination_distance_miles,
                    )
                    for item in feature.lane_history
                    if item.version_id in versions
                ],
                "equipment": [
                    _evidence_summary(versions[version_id])
                    for version_id in feature.equipment_history_version_ids
                    if version_id in versions
                ],
                "recency": [
                    _evidence_summary(versions[version_id], tier=lane_tiers.get(version_id))
                    for version_id in feature.relevant_completed_version_ids
                    if version_id in versions
                ],
                "deadhead": (
                    []
                    if (
                        feature.last_delivery_load_version_id is None
                        or feature.last_delivery_load_version_id not in versions
                    )
                    else [_evidence_summary(versions[feature.last_delivery_load_version_id])]
                ),
            },
        }
    return result


def _pricing_evidence(
    session: Session, tenant_id: UUID, estimate: RateEstimate, as_of: datetime
) -> list[dict[str, Any]]:
    """Persist readable, tenant-local rate-comparison evidence in one batch."""
    version_ids = tuple(item.load_version_id for item in estimate.comparables)
    versions = {
        version.id: version
        for version in session.scalars(
            select(LoadVersion).where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.id.in_(version_ids),
                LoadVersion.observed_at <= as_of,
            )
        ).all()
    }
    summaries: list[dict[str, Any]] = []
    for item in estimate.comparables:
        version = versions.get(item.load_version_id)
        readable = (
            _evidence_summary(version, tier=item.tier.value)
            if version is not None
            else {
                "load_external_id": item.load_external_id,
                "route": "Route unavailable",
                "equipment": None,
                "completed_observed_at": None,
                "distance_miles": None,
                "tier": item.tier.value,
            }
        )
        summaries.append(
            {
                **readable,
                # Immutable IDs remain in the stored audit payload only. API output uses
                # the readable source-load summary above.
                "load_id": str(item.load_id),
                "load_version_id": str(item.load_version_id),
                "carrier_rate_usd": str(item.carrier_rate_usd),
                "origin_distance_miles": item.origin_distance_miles,
                "destination_distance_miles": item.destination_distance_miles,
                "route_mile_difference": None
                if item.route_mile_difference is None
                else str(item.route_mile_difference),
                "recency_days": item.recency_days,
                "weight": str(item.weight),
                "evidence_ids": list(item.evidence_ids),
            }
        )
    return summaries


def _evidence_summary(
    version: LoadVersion,
    *,
    tier: str | None = None,
    origin_distance_miles: float | None = None,
    destination_distance_miles: float | None = None,
) -> dict[str, object]:
    snapshot = version.canonical_snapshot
    stops = snapshot.get("stops")
    locations: list[str] = []
    if isinstance(stops, list):
        for raw_stop in cast(list[object], stops):
            if isinstance(raw_stop, dict):
                stop = cast(dict[str, object], raw_stop)
                city, state = stop.get("city"), stop.get("state")
                if isinstance(city, str) and isinstance(state, str):
                    locations.append(f"{city.title()}, {state}")
    external_id = snapshot.get("external_id")
    result: dict[str, object] = {
        # Keep the immutable version identifier in the persisted audit payload so
        # the API can re-authorize this human-readable summary at read time.
        # It is deliberately not part of the public response schema.
        "load_version_id": str(version.id),
        # A missing source ID should be visibly incomplete, not fall back to a database UUID.
        "load_external_id": external_id if isinstance(external_id, str) else "Historical load",
        "route": (
            " → ".join((locations[0], locations[-1]))
            if len(locations) >= 2
            else "Route unavailable"
        ),
        "equipment": "UNKNOWN" if version.equipment is None else version.equipment.value,
        "completed_observed_at": version.observed_at.isoformat(),
        "distance_miles": None if version.distance_miles is None else str(version.distance_miles),
    }
    if tier is not None:
        result["tier"] = tier
    if origin_distance_miles is not None:
        result["origin_distance_miles"] = origin_distance_miles
    if destination_distance_miles is not None:
        result["destination_distance_miles"] = destination_distance_miles
    return result
