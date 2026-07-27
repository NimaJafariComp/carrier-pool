"""Analysis-only rate baseline contracts."""

from decimal import Decimal

from carrier_pool.decisioning.baselines import (
    BaselineObservation,
    BaselineTarget,
    compare_baselines,
    equipment_distance_band_median,
    quantile_regression,
    robust_huber_regression,
    tenant_wide_median,
    unshrunk_nearest_lane_weighted_median,
)
from carrier_pool.domain.types import EquipmentType


def observation(
    rate: str,
    distance: str,
    *,
    equipment: EquipmentType = EquipmentType.DRY_VAN,
    weight: str = "1",
) -> BaselineObservation:
    return BaselineObservation(Decimal(rate), equipment, Decimal(distance), Decimal(weight))


def test_simple_baselines_are_tenant_local_and_deterministic() -> None:
    rows = (
        observation("1000", "200", weight=".8"),
        observation("1200", "250", weight=".2"),
        observation("2000", "800", equipment=EquipmentType.REEFER),
    )
    target = BaselineTarget(EquipmentType.DRY_VAN, Decimal("240"))

    tenant = tenant_wide_median(rows)
    equipment_band = equipment_distance_band_median(target, rows)
    lane = unshrunk_nearest_lane_weighted_median(rows[:2])

    assert tenant is not None
    assert equipment_band is not None
    assert lane is not None
    assert tenant.point_estimate_usd == Decimal("1200")
    assert equipment_band.point_estimate_usd == Decimal("1000")
    assert lane.point_estimate_usd == Decimal("1000")


def test_regression_baselines_require_minimum_evidence() -> None:
    rows = tuple(observation(str(1000 + index * 10), str(100 + index * 5)) for index in range(7))
    target = BaselineTarget(EquipmentType.DRY_VAN, Decimal("150"))

    assert robust_huber_regression(target, rows) is None
    assert quantile_regression(target, rows) is None


def test_regression_baselines_return_decimal_estimates_when_supported() -> None:
    rows = tuple(observation(str(1000 + index * 10), str(100 + index * 5)) for index in range(24))
    target = BaselineTarget(EquipmentType.DRY_VAN, Decimal("150"))

    huber = robust_huber_regression(target, rows)
    quantiles = quantile_regression(target, rows)

    assert huber is not None
    assert quantiles is not None
    assert huber.model_name == "robust_huber_regression"
    assert quantiles.lower_usd is not None
    assert quantiles.upper_usd is not None
    assert quantiles.lower_usd <= quantiles.point_estimate_usd <= quantiles.upper_usd


def test_comparison_reports_metrics_without_selecting_complex_model() -> None:
    actuals = (Decimal("1000"), Decimal("1200"))
    predictions = {
        "tenant_wide_median": (Decimal("1100"), Decimal("1100")),
        "robust_huber_regression": (Decimal("1001"), Decimal("1199")),
    }

    comparison = compare_baselines(actuals, predictions)

    assert comparison["tenant_wide_median"].mae_usd == Decimal("100")
    assert comparison["robust_huber_regression"].mae_usd == Decimal("1")
