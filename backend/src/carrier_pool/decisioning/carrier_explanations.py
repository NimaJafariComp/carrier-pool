"""Fixed-template explanations for historical-fit carrier rankings."""

from dataclasses import dataclass
from decimal import Decimal

from carrier_pool.decisioning.carrier_features import CarrierFeatureSet
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFit
from carrier_pool.geography.comparables import LaneTier


@dataclass(frozen=True, slots=True)
class RankedCarrierExplanation:
    rank: int
    carrier_external_id: str
    adjusted_score: Decimal
    confidence: str
    component_scores: dict[str, Decimal | None]
    evidence_bullets: tuple[str, ...]
    supporting_load_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    model_version: str
    evidence_status: str
    tie_group: int | None


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
        bullets.extend(_lane_bullets(feature))
        if feature.equipment_history_count:
            bullets.append(
                f"{feature.equipment_history_count} completed equipment-matching loads "
                "are recorded."
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
                item.evidence_status,
                item.tie_group,
            )
        )
    return tuple(result)


def _lane_bullets(feature: CarrierFeatureSet) -> tuple[str, ...]:
    directional = sum(
        1
        for item in feature.lane_history
        if item.tier in {LaneTier.NEAR_EXACT, LaneTier.REGIONAL, LaneTier.METRO_CORRIDOR}
    )
    distance_equipment = sum(
        1 for item in feature.lane_history if item.tier is LaneTier.DISTANCE_EQUIPMENT
    )
    tenant_equipment = sum(
        1 for item in feature.lane_history if item.tier is LaneTier.TENANT_EQUIPMENT
    )
    tenant_all = sum(
        1 for item in feature.lane_history if item.tier is LaneTier.TENANT_ALL_EQUIPMENT
    )
    bullets: list[str] = []
    if directional:
        bullets.append(f"{directional} completed directional lane matches are recorded.")
    if distance_equipment:
        noun = "load" if distance_equipment == 1 else "loads"
        bullets.append(
            f"{distance_equipment} completed {noun} had a similar trip length and required "
            "the same equipment."
        )
    if tenant_equipment:
        bullets.append(f"{tenant_equipment} completed same-equipment tenant loads are recorded.")
    if tenant_all:
        bullets.append(f"{tenant_all} broader tenant-history loads are recorded.")
    return tuple(bullets)
