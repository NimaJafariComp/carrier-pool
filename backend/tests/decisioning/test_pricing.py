"""Hierarchical rate-estimation service contracts without database fixtures."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from carrier_pool.decisioning.pricing import (
    ConfidenceLevel,
    HierarchicalRateEstimator,
    PricingTarget,
)
from carrier_pool.domain.types import EquipmentType
from carrier_pool.geography.comparables import ComparableLoadEvidence, LaneTier

AS_OF = datetime(2026, 7, 11, 6, tzinfo=UTC)
TENANT_ID = uuid4()
TARGET_ID = uuid4()
TARGET_VERSION_ID = uuid4()


def evidence(
    tier: LaneTier,
    *,
    origin: float | None = 5,
    destination: float | None = 5,
    route_difference: str | None = "5",
    recency_days: float = 2,
) -> ComparableLoadEvidence:
    version_id = uuid4()
    return ComparableLoadEvidence(
        load_id=uuid4(),
        load_external_id=f"load-{version_id}",
        version_id=version_id,
        equipment=EquipmentType.DRY_VAN,
        tier=tier,
        origin_distance_miles=origin,
        destination_distance_miles=destination,
        route_mile_difference=None if route_difference is None else Decimal(route_difference),
        recency_days=recency_days,
        evidence_ids=(str(version_id),),
    )


class StubEstimator(HierarchicalRateEstimator):
    def __init__(
        self,
        target: PricingTarget,
        tiers: dict[LaneTier, tuple[ComparableLoadEvidence, ...]],
        rates: dict[UUID, Decimal],
    ) -> None:
        super().__init__()
        self.target = target
        self.tiers = tiers
        self.rates = rates

    def _target_at_as_of(
        self, session: object, tenant_id: UUID, load_id: UUID, as_of: datetime
    ) -> PricingTarget:  # type: ignore[override]
        return self.target

    def _comparables_by_tier(
        self,
        session: object,
        tenant_id: UUID,
        target: PricingTarget,
        as_of: datetime,
    ) -> dict[LaneTier, tuple[ComparableLoadEvidence, ...]]:  # type: ignore[override]
        return self.tiers

    def _carrier_rates_at_as_of(
        self,
        session: object,
        tenant_id: UUID,
        evidence: tuple[ComparableLoadEvidence, ...],
        as_of: datetime,
    ) -> dict[UUID, tuple[Decimal, tuple[str, ...]]]:  # type: ignore[override]
        return {
            item.version_id: (self.rates[item.version_id], item.evidence_ids)
            for item in evidence
            if item.version_id in self.rates
        }


def stub(
    tiers: dict[LaneTier, tuple[ComparableLoadEvidence, ...]],
    rates: dict[UUID, Decimal],
    equipment: EquipmentType = EquipmentType.DRY_VAN,
) -> StubEstimator:
    return StubEstimator(PricingTarget(TARGET_VERSION_ID, equipment), tiers, rates)


def estimate(estimator: StubEstimator):
    return estimator.estimate(object(), TENANT_ID, TARGET_ID, AS_OF)


def test_exact_tier_returns_decimal_estimate_range_and_structured_evidence() -> None:
    first, second = evidence(LaneTier.NEAR_EXACT), evidence(LaneTier.NEAR_EXACT)
    result = estimate(
        stub(
            {LaneTier.NEAR_EXACT: (first, second)},
            {first.version_id: Decimal("1000"), second.version_id: Decimal("1200")},
        )
    )

    assert result.point_estimate_usd == Decimal("1000")
    assert result.historical_comparison_lower_usd == Decimal("1000")
    assert result.historical_comparison_upper_usd == Decimal("1200")
    assert result.local_tier is LaneTier.NEAR_EXACT
    assert result.raw_evidence_count == 2
    assert len(result.comparables) == 2
    assert result.model_version == "pricing-hierarchical-v1"


def test_regional_tier_is_used_when_exact_history_is_absent() -> None:
    item = evidence(LaneTier.REGIONAL)
    result = estimate(stub({LaneTier.REGIONAL: (item,)}, {item.version_id: Decimal("1100")}))

    assert result.point_estimate_usd == Decimal("1100")
    assert result.local_tier is LaneTier.REGIONAL
    assert result.broader_tier is None


def test_rich_exact_history_does_not_require_broader_blending() -> None:
    local = tuple(evidence(LaneTier.NEAR_EXACT) for _ in range(4))
    broad = evidence(LaneTier.REGIONAL)
    result = estimate(
        stub(
            {LaneTier.NEAR_EXACT: local, LaneTier.REGIONAL: (broad,)},
            {
                **{item.version_id: Decimal("1000") for item in local},
                broad.version_id: Decimal("1400"),
            },
        )
    )

    assert result.point_estimate_usd == Decimal("1000")
    assert result.broader_tier is None
    assert "BROADER_FALLBACK" not in result.warnings


def test_sparse_local_history_blends_with_metro_baseline() -> None:
    local = evidence(LaneTier.REGIONAL)
    broad_first, broad_second = evidence(LaneTier.METRO_CORRIDOR), evidence(LaneTier.METRO_CORRIDOR)
    result = estimate(
        stub(
            {LaneTier.REGIONAL: (local,), LaneTier.METRO_CORRIDOR: (broad_first, broad_second)},
            {
                local.version_id: Decimal("1000"),
                broad_first.version_id: Decimal("1200"),
                broad_second.version_id: Decimal("1300"),
            },
        )
    )

    assert result.local_tier is LaneTier.REGIONAL
    assert result.broader_tier is LaneTier.METRO_CORRIDOR
    assert result.blend_local_weight == Decimal("0.1428571428571428571428571429")
    assert result.point_estimate_usd == Decimal("1171.428571428571428571428572")
    assert result.raw_evidence_count == len(result.comparables) == 3
    assert abs(result.effective_evidence_count - Decimal("3")) < Decimal("0.000001")
    assert "SPARSE_EVIDENCE" in result.warnings
    assert "BROADER_FALLBACK" in result.warnings


def test_unknown_equipment_caps_confidence_and_records_warning() -> None:
    item = evidence(LaneTier.TENANT_ALL_EQUIPMENT, origin=None, destination=None)
    result = estimate(
        stub(
            {LaneTier.TENANT_ALL_EQUIPMENT: (item,)},
            {item.version_id: Decimal("1400")},
            EquipmentType.UNKNOWN,
        )
    )

    assert result.confidence.level is ConfidenceLevel.LOW
    assert "UNKNOWN_EQUIPMENT" in result.warnings
    assert "MISSING_GEOGRAPHY" in result.warnings


def test_no_eligible_rate_returns_explicit_warning_without_an_estimate() -> None:
    item = evidence(LaneTier.NEAR_EXACT)
    result = estimate(stub({LaneTier.NEAR_EXACT: (item,)}, {}))

    assert result.point_estimate_usd is None
    assert result.historical_comparison_lower_usd is None
    assert result.comparables == ()
    assert result.warnings == ("NO_HISTORICAL_EVIDENCE",)


def test_estimator_requires_timezone_aware_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        stub({}, {}).estimate(object(), TENANT_ID, TARGET_ID, datetime(2026, 7, 11))
