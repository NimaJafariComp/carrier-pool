"""Analysis-only transparent and regression baselines for rate backtests."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from carrier_pool.domain.types import EquipmentType

DISTANCE_BAND_MILES = Decimal(100)
HUBER_MIN_SAMPLES = 8
QUANTILE_MIN_SAMPLES = 20


@dataclass(frozen=True, slots=True)
class BaselineObservation:
    """One tenant-local completed historical rate available at a cutoff."""

    carrier_rate_usd: Decimal
    equipment: EquipmentType | None
    distance_miles: Decimal | None
    lane_weight: Decimal = Decimal(1)


@dataclass(frozen=True, slots=True)
class BaselineTarget:
    """Only target features allowed into a baseline prediction."""

    equipment: EquipmentType | None
    distance_miles: Decimal | None


@dataclass(frozen=True, slots=True)
class BaselinePrediction:
    """Backtest-only point/range prediction; never a production response."""

    model_name: str
    point_estimate_usd: Decimal
    lower_usd: Decimal | None = None
    upper_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class BaselineMetrics:
    """Comparable point-error metrics for one backtest-only model."""

    mae_usd: Decimal
    median_absolute_error_usd: Decimal
    wape: Decimal | None


def tenant_wide_median(observations: Sequence[BaselineObservation]) -> BaselinePrediction | None:
    """Return lower median of all same-tenant historical totals."""
    median = _lower_median(tuple(item.carrier_rate_usd for item in observations))
    return None if median is None else BaselinePrediction("tenant_wide_median", median)


def equipment_distance_band_median(
    target: BaselineTarget, observations: Sequence[BaselineObservation]
) -> BaselinePrediction | None:
    """Return lower median for same equipment within a fixed ±100-mile band."""
    if target.distance_miles is None:
        return None
    rates = tuple(
        item.carrier_rate_usd
        for item in observations
        if item.equipment == target.equipment
        and item.distance_miles is not None
        and abs(item.distance_miles - target.distance_miles) <= DISTANCE_BAND_MILES
    )
    median = _lower_median(rates)
    return None if median is None else BaselinePrediction("equipment_distance_band_median", median)


def unshrunk_nearest_lane_weighted_median(
    observations: Sequence[BaselineObservation],
) -> BaselinePrediction | None:
    """Return a weighted lane median without hierarchy or shrinkage."""
    positive = sorted(
        (item for item in observations if item.lane_weight > 0),
        key=lambda item: item.carrier_rate_usd,
    )
    total_weight = sum((item.lane_weight for item in positive), Decimal(0))
    if total_weight == 0:
        return None
    cumulative = Decimal(0)
    for item in positive:
        cumulative += item.lane_weight
        if cumulative >= total_weight * Decimal("0.5"):
            return BaselinePrediction(
                "unshrunk_nearest_lane_weighted_median", item.carrier_rate_usd
            )
    raise AssertionError("Positive cumulative weight did not reach total.")


def robust_huber_regression(
    target: BaselineTarget, observations: Sequence[BaselineObservation]
) -> BaselinePrediction | None:
    """Fit analysis-only Huber regression using distance and equipment indicator."""
    usable = _usable_regression_rows(observations)
    if target.distance_miles is None or len(usable) < HUBER_MIN_SAMPLES:
        return None
    from sklearn.linear_model import HuberRegressor

    model = HuberRegressor().fit(_features(usable), _targets(usable))
    point = Decimal(str(model.predict([_feature(target.equipment, target.distance_miles)])[0]))
    return BaselinePrediction("robust_huber_regression", point)


def quantile_regression(
    target: BaselineTarget, observations: Sequence[BaselineObservation]
) -> BaselinePrediction | None:
    """Fit analysis-only q25/q50/q75 linear models when data supports them."""
    usable = _usable_regression_rows(observations)
    if target.distance_miles is None or len(usable) < QUANTILE_MIN_SAMPLES:
        return None
    from sklearn.linear_model import QuantileRegressor

    features = _features(usable)
    values = _targets(usable)
    target_feature = [_feature(target.equipment, target.distance_miles)]
    predictions = []
    for quantile in (0.25, 0.5, 0.75):
        model = QuantileRegressor(quantile=quantile, alpha=0).fit(features, values)
        predictions.append(Decimal(str(model.predict(target_feature)[0])))
    lower, point, upper = predictions
    return BaselinePrediction(
        "quantile_regression", point, lower_usd=min(lower, point), upper_usd=max(upper, point)
    )


def compare_baselines(
    actuals: Sequence[Decimal], predictions: Mapping[str, Sequence[Decimal]]
) -> dict[str, BaselineMetrics]:
    """Compare aligned rolling labels/predictions without choosing a winner."""
    if not actuals:
        return {}
    result: dict[str, BaselineMetrics] = {}
    actual_total = sum(actuals, Decimal(0))
    for name, values in predictions.items():
        if len(values) != len(actuals):
            raise ValueError("Baseline predictions must align one-for-one with actuals.")
        errors = tuple(
            abs(prediction - actual) for prediction, actual in zip(values, actuals, strict=True)
        )
        result[name] = BaselineMetrics(
            mae_usd=sum(errors, Decimal(0)) / Decimal(len(errors)),
            median_absolute_error_usd=_median(errors),
            wape=(sum(errors, Decimal(0)) / actual_total) if actual_total else None,
        )
    return result


def _lower_median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sorted(values)[(len(values) - 1) // 2]


def _usable_regression_rows(
    observations: Sequence[BaselineObservation],
) -> tuple[BaselineObservation, ...]:
    return tuple(item for item in observations if item.distance_miles is not None)


def _features(rows: Sequence[BaselineObservation]) -> list[list[float]]:
    return [
        _feature(item.equipment, item.distance_miles)
        for item in rows
        if item.distance_miles is not None
    ]


def _targets(rows: Sequence[BaselineObservation]) -> list[float]:
    return [float(item.carrier_rate_usd) for item in rows]


def _feature(equipment: EquipmentType | None, distance_miles: Decimal) -> list[float]:
    return [float(distance_miles), float(_equipment_code(equipment))]


def _equipment_code(equipment: EquipmentType | None) -> int:
    return {
        EquipmentType.DRY_VAN: 0,
        EquipmentType.REEFER: 1,
        EquipmentType.FLATBED: 2,
        EquipmentType.UNKNOWN: 3,
        None: 3,
    }[equipment]


def _median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)
