"""Fixed-template explanations for historical-fit carrier rankings."""

from dataclasses import dataclass
from decimal import Decimal

from carrier_pool.decisioning.carrier_features import CarrierFeatureSet
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFit


@dataclass(frozen=True, slots=True)
class RankedCarrierExplanation:
    rank: int
    carrier_external_id: str
    adjusted_score: Decimal
    confidence: str
    component_scores: dict[str, Decimal]
    evidence_bullets: tuple[str, ...]
    supporting_load_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    model_version: str


def explain_rankings(
    rankings: tuple[CarrierHistoricalFit, ...], features: tuple[CarrierFeatureSet, ...]
) -> tuple[RankedCarrierExplanation, ...]:
    """Render only structured, historical evidence; never operational predictions."""
    by_carrier = {item.carrier_external_id: item for item in features}
    result: list[RankedCarrierExplanation] = []
    for rank, item in enumerate(rankings, start=1):
        feature = by_carrier[item.carrier_external_id]
        warnings = list(item.warnings)
        bullets: list[str] = []
        if feature.lane_history:
            bullets.append(
                f"{len(feature.lane_history)} completed directional historical loads "
                "support this fit."
            )
        if feature.equipment_history_count:
            bullets.append(
                f"{feature.equipment_history_count} completed equipment-matching loads "
                "are recorded."
            )
        if feature.last_delivery_observed_at is not None:
            bullets.append(
                "A last known historical delivery is included as evidence, not live location."
            )
        if feature.target_equipment_unknown:
            warnings.append("UNKNOWN_TARGET_EQUIPMENT")
            bullets.append("Target equipment is unknown, so equipment-fit confidence is limited.")
        if item.effective_history < 4:
            warnings.append("SPARSE_HISTORY_SHRINKAGE")
            bullets.append("Limited completed history pulls the score toward a neutral prior.")
        result.append(
            RankedCarrierExplanation(
                rank,
                item.carrier_external_id,
                item.adjusted_score,
                item.confidence,
                item.component_scores,
                tuple(bullets),
                feature.raw_evidence_ids,
                tuple(sorted(set(warnings))),
                item.model_version,
            )
        )
    return tuple(result)
