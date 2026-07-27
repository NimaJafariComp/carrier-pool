"""Deterministic historical-fit carrier scoring; never an availability model."""

from dataclasses import dataclass
from decimal import Decimal
from math import exp

from carrier_pool.decisioning.carrier_features import CarrierFeatureSet
from carrier_pool.geography.comparables import LaneTier

MODEL_VERSION = "carrier-ranking-v1"
_TIER = {
    LaneTier.NEAR_EXACT: 1.0,
    LaneTier.REGIONAL: 0.8,
    LaneTier.METRO_CORRIDOR: 0.6,
    LaneTier.DISTANCE_EQUIPMENT: 0.45,
    LaneTier.TENANT_EQUIPMENT: 0.3,
    LaneTier.TENANT_ALL_EQUIPMENT: 0.15,
}


@dataclass(frozen=True, slots=True)
class CarrierHistoricalFit:
    carrier_external_id: str
    raw_score: Decimal
    adjusted_score: Decimal
    confidence_score: Decimal
    confidence: str
    component_scores: dict[str, Decimal]
    effective_history: Decimal
    warnings: tuple[str, ...]
    model_version: str = MODEL_VERSION


class CarrierHistoricalFitScorer:
    """Score supplied immutable features with documented shrinkage and tie-breakers."""

    def score(self, candidates: tuple[CarrierFeatureSet, ...]) -> tuple[CarrierHistoricalFit, ...]:
        results = tuple(self._score(item) for item in candidates)
        return tuple(
            sorted(
                results,
                key=lambda item: (
                    -item.adjusted_score,
                    -item.confidence_score,
                    item.carrier_external_id,
                ),
            )
        )

    def _score(self, item: CarrierFeatureSet) -> CarrierHistoricalFit:
        lane = (
            Decimal(
                str(
                    sum(_TIER[e.tier] * exp(-e.recency_days / 30) for e in item.lane_history)
                    / len(item.lane_history)
                )
            )
            if item.lane_history
            else Decimal(0)
        )
        equipment = Decimal(item.equipment_history_count) / Decimal(
            max(1, item.completed_history_count)
        )
        deadhead = (
            Decimal(0)
            if item.delivery_to_pickup_miles is None or item.delivery_to_pickup_gap_days is None
            else Decimal(
                str(
                    exp(-item.delivery_to_pickup_miles / 75)
                    * exp(-item.delivery_to_pickup_gap_days / 14)
                )
            )
        )
        recency_days = (
            365.0
            if item.relevant_completed_observed_at is None
            else (
                item.last_delivery_observed_at - item.relevant_completed_observed_at
            ).total_seconds()
            / 86400
            if item.last_delivery_observed_at
            else 365.0
        )
        recency = Decimal(str(exp(-max(0.0, recency_days) / 30)))
        raw = Decimal(100) * (
            Decimal(".4") * lane
            + Decimal(".2") * equipment
            + Decimal(".2") * deadhead
            + Decimal(".2") * recency
        )
        ess = Decimal(min(8, len(item.lane_history) + item.equipment_history_count * 0.5 + 1))
        alpha = ess / (ess + Decimal(6))
        adjusted = alpha * raw + (Decimal(1) - alpha) * Decimal(50)
        confidence_score = (
            Decimal(".45") * min(Decimal(1), ess / Decimal(6))
            + Decimal(".2") * lane
            + Decimal(".15") * recency
            + Decimal(".1") * equipment
            + (Decimal(".1") if item.delivery_to_pickup_miles is not None else Decimal(0))
        )
        if item.target_equipment_unknown:
            confidence_score = min(confidence_score, Decimal(".44"))
        confidence = (
            "HIGH"
            if confidence_score >= Decimal(".75")
            else "MEDIUM"
            if confidence_score >= Decimal(".45")
            else "LOW"
        )
        warnings = ("SPARSE_HISTORY_SHRINKAGE",) if ess < 4 else ()
        return CarrierHistoricalFit(
            item.carrier_external_id,
            raw,
            adjusted,
            confidence_score,
            confidence,
            {"lane": lane, "equipment": equipment, "deadhead": deadhead, "recency": recency},
            ess,
            warnings,
        )
