"""Deterministic historical-fit carrier scoring; never an availability model."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from math import exp
from typing import Literal
from uuid import UUID

from carrier_pool.decisioning.carrier_features import CarrierFeatureSet
from carrier_pool.geography.comparables import ComparableLoadEvidence, LaneTier

MODEL_VERSION = "carrier-ranking-v5"
LEGACY_MODEL_VERSION = "carrier-ranking-v4"
CALIBRATED_CANDIDATE_MODEL_VERSION = "carrier-ranking-v6"
DEFAULT_SHRINKAGE_STRENGTH = Decimal(6)
V6_SHRINKAGE_STRENGTH = Decimal(4)
TIE_SCORE_MARGIN = Decimal("2")
_TIER: dict[LaneTier, Decimal] = {
    LaneTier.NEAR_EXACT: Decimal("1.00"),
    LaneTier.REGIONAL: Decimal(".80"),
    LaneTier.METRO_CORRIDOR: Decimal(".60"),
    LaneTier.DISTANCE_EQUIPMENT: Decimal(".45"),
    LaneTier.TENANT_EQUIPMENT: Decimal(".30"),
    LaneTier.TENANT_ALL_EQUIPMENT: Decimal(".15"),
}


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    """Documented component weights, reusable for analysis-only ablations."""

    lane: Decimal = Decimal(".4")
    equipment: Decimal = Decimal(".2")
    deadhead: Decimal = Decimal(".2")
    recency: Decimal = Decimal(".2")

    def __post_init__(self) -> None:
        values = self.as_dict().values()
        if any(value < 0 for value in values) or not any(values):
            raise ValueError("scoring weights must be non-negative with one positive component.")

    def as_dict(self) -> dict[str, Decimal]:
        return {
            "lane": self.lane,
            "equipment": self.equipment,
            "deadhead": self.deadhead,
            "recency": self.recency,
        }

    def without(self, component: str) -> "ScoringWeights":
        if component not in self.as_dict():
            raise ValueError(f"unknown scoring component: {component}")
        return replace(self, **{component: Decimal(0)})


@dataclass(frozen=True, slots=True)
class CarrierHistoricalFit:
    carrier_external_id: str
    raw_score: Decimal
    adjusted_score: Decimal
    confidence_score: Decimal
    confidence: str
    component_scores: dict[str, Decimal | None]
    effective_history: Decimal
    warnings: tuple[str, ...]
    evidence_status: str
    tie_group: int | None = None
    model_version: str = MODEL_VERSION
    relevant_completed_observed_at: datetime | None = None


class CarrierHistoricalFitScorer:
    """Score supplied immutable features with documented shrinkage and tie-breakers."""

    def __init__(
        self,
        weights: ScoringWeights | None = None,
        *,
        history_mode: Literal["identity", "legacy"] = "identity",
        shrinkage_strength: Decimal = DEFAULT_SHRINKAGE_STRENGTH,
    ) -> None:
        self._weights = weights or ScoringWeights()
        self._history_mode = history_mode
        if shrinkage_strength <= 0:
            raise ValueError("shrinkage_strength must be positive.")
        self._shrinkage_strength = shrinkage_strength

    def score(self, candidates: tuple[CarrierFeatureSet, ...]) -> tuple[CarrierHistoricalFit, ...]:
        results = tuple(self._score(item) for item in candidates)
        ordered = tuple(
            sorted(
                results,
                key=lambda item: (
                    item.evidence_status != "SUPPORTED",
                    -item.adjusted_score,
                    -item.confidence_score,
                    -item.effective_history,
                    -_timestamp(item.relevant_completed_observed_at),
                    item.carrier_external_id,
                ),
            )
        )
        return _assign_tie_groups(ordered)

    def _score(self, item: CarrierFeatureSet) -> CarrierHistoricalFit:
        lane_weights = tuple(_lane_weight(evidence) for evidence in item.lane_history)
        lane = _lane_score(item.lane_history, lane_weights)
        equipment = Decimal("0.5") if item.target_equipment_unknown else _equipment_fit(item)
        deadhead = (
            _decimal(
                exp(-item.delivery_to_pickup_miles / 75)
                * exp(-item.delivery_to_pickup_gap_days / 14)
            )
            if (
                item.delivery_to_pickup_miles is not None
                and item.delivery_to_pickup_gap_days is not None
            )
            else None
        )
        recency = _recency_fit(item)
        components: dict[str, Decimal | None] = {
            "lane": lane,
            "equipment": equipment,
            "deadhead": deadhead,
            "recency": recency,
        }
        weights = self._weights.as_dict()
        available: dict[str, Decimal] = {
            name: value
            for name, value in components.items()
            if value is not None and weights[name] > 0
        }
        raw = (
            Decimal(100)
            * sum(weights[name] * value for name, value in available.items())
            / sum(weights[name] for name in available)
            if available
            else Decimal(50)
        )
        ess = (
            _legacy_effective_history(item, lane_weights)
            if self._history_mode == "legacy"
            else _identity_effective_history(item, lane_weights)
        )
        alpha = ess / (ess + self._shrinkage_strength)
        adjusted = alpha * raw + (Decimal(1) - alpha) * Decimal(50)
        confidence_ess = min(Decimal(8), ess)
        geography_completeness = Decimal(
            any(
                evidence.origin_distance_miles is not None
                and evidence.destination_distance_miles is not None
                for evidence in item.lane_history
            )
        )
        equipment_coverage = (
            Decimal(".5")
            if item.target_equipment_unknown
            else min(Decimal(1), Decimal(item.equipment_history_count) / Decimal(3))
        )
        confidence_score = (
            Decimal(".45") * min(Decimal(1), confidence_ess / Decimal(6))
            + Decimal(".2") * (lane or Decimal(0))
            + Decimal(".15") * (recency or Decimal(0))
            + Decimal(".1") * equipment_coverage
            + Decimal(".1") * geography_completeness
        )
        if item.target_equipment_unknown or ess < 1:
            confidence_score = min(confidence_score, Decimal(".44"))
        confidence = (
            "HIGH"
            if confidence_score >= Decimal(".75")
            else "MEDIUM"
            if confidence_score >= Decimal(".45")
            else "LOW"
        )
        warnings: list[str] = []
        if ess < 4:
            warnings.append("SPARSE_HISTORY_SHRINKAGE")
        if item.target_equipment_unknown:
            warnings.append("UNKNOWN_TARGET_EQUIPMENT")
        if deadhead is None:
            warnings.append("DEADHEAD_LOCATION_UNAVAILABLE")
        if item.has_broad_recency_evidence:
            warnings.append("BROAD_RECENCY_EVIDENCE")
        evidence_status = (
            "SUPPORTED" if lane is not None or item.equipment_history_count > 0 else "LIMITED"
        )
        if evidence_status == "LIMITED":
            warnings.append("LIMITED_RELEVANT_HISTORY")
        return CarrierHistoricalFit(
            item.carrier_external_id,
            raw,
            adjusted,
            confidence_score,
            confidence,
            components,
            ess,
            tuple(warnings),
            evidence_status,
            model_version=self.model_version,
            relevant_completed_observed_at=item.relevant_completed_observed_at,
        )

    @property
    def model_version(self) -> str:
        if self._history_mode == "legacy":
            return LEGACY_MODEL_VERSION
        if self._shrinkage_strength == V6_SHRINKAGE_STRENGTH:
            return CALIBRATED_CANDIDATE_MODEL_VERSION
        return MODEL_VERSION


def _lane_score(
    evidence: tuple[ComparableLoadEvidence, ...], weights: tuple[Decimal, ...]
) -> Decimal | None:
    if not weights:
        return None
    weighted_quality = sum(
        (weight * _TIER[item.tier] for item, weight in zip(evidence, weights, strict=True)),
        Decimal(0),
    )
    return (
        weighted_quality
        / sum(weights, Decimal(0))
        * min(Decimal(1), _kish_ess(weights) / Decimal(4))
    )


def _lane_weight(item: ComparableLoadEvidence) -> Decimal:
    terms = (item.origin_distance_miles, item.destination_distance_miles)
    weight = 1.0
    for distance in terms:
        if distance is not None:
            weight *= exp(-distance / 25)
    if item.route_mile_difference is not None:
        weight *= exp(-float(item.route_mile_difference) / 50)
    weight *= exp(-item.recency_days / 30)
    return _decimal(weight)


def _kish_ess(weights: tuple[Decimal, ...]) -> Decimal:
    if not weights:
        return Decimal(0)
    total = sum(weights, Decimal(0))
    squared_total = sum((weight * weight for weight in weights), Decimal(0))
    return total**2 / squared_total


def _legacy_effective_history(
    item: CarrierFeatureSet, lane_weights: tuple[Decimal, ...]
) -> Decimal:
    """Pre-v5 aggregation, retained for same-case calibration comparisons only."""
    return min(
        Decimal(8),
        _kish_ess(lane_weights)
        + Decimal(".5") * item.equipment_history_count
        + Decimal(".5") * item.relevant_completed_count,
    )


def _identity_effective_history(
    item: CarrierFeatureSet, lane_weights: tuple[Decimal, ...]
) -> Decimal:
    """Kish ESS over unique completed versions, never counting a load per component."""
    weights: dict[UUID, Decimal] = {}
    for evidence, weight in zip(item.lane_history, lane_weights, strict=True):
        weights[evidence.version_id] = max(weights.get(evidence.version_id, Decimal(0)), weight)
    if item.equipment_history_version_ids:
        for version_id, age_days in zip(
            item.equipment_history_version_ids, item.equipment_history_age_days, strict=True
        ):
            weight = _decimal(exp(-age_days / 45))
            weights[version_id] = max(weights.get(version_id, Decimal(0)), weight)
    for version_id in item.relevant_completed_version_ids:
        weights.setdefault(version_id, _decimal(exp(-(item.relevant_completed_age_days or 0) / 30)))
    return _kish_ess(tuple(weights.values()))


def _equipment_fit(item: CarrierFeatureSet) -> Decimal | None:
    if (
        item.completed_history_count == 0
        or not item.equipment_history_age_days
        or not item.completed_history_age_days
    ):
        return None
    exact_weight = sum(exp(-age_days / 45) for age_days in item.equipment_history_age_days)
    total_weight = sum(exp(-age_days / 45) for age_days in item.completed_history_age_days)
    return _decimal(exact_weight / total_weight)


def _recency_fit(item: CarrierFeatureSet) -> Decimal | None:
    if item.relevant_completed_age_days is None:
        return None
    return _decimal(exp(-item.relevant_completed_age_days / 30))


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _timestamp(value: datetime | None) -> float:
    return value.timestamp() if value is not None else float("-inf")


def _assign_tie_groups(
    rankings: tuple[CarrierHistoricalFit, ...],
) -> tuple[CarrierHistoricalFit, ...]:
    """Group close supported scores; limited-history candidates are not call-order groups."""
    result: list[CarrierHistoricalFit] = []
    group = 0
    group_anchor: Decimal | None = None
    for item in rankings:
        if item.evidence_status != "SUPPORTED":
            result.append(item)
            continue
        if group_anchor is None or group_anchor - item.adjusted_score > TIE_SCORE_MARGIN:
            group += 1
            group_anchor = item.adjusted_score
        result.append(
            CarrierHistoricalFit(
                item.carrier_external_id,
                item.raw_score,
                item.adjusted_score,
                item.confidence_score,
                item.confidence,
                item.component_scores,
                item.effective_history,
                item.warnings,
                item.evidence_status,
                group,
                item.model_version,
                item.relevant_completed_observed_at,
            )
        )
    return tuple(result)
