"""Contracts for pure pricing weighted-statistics primitives."""

from decimal import Decimal

import pytest

from carrier_pool.decisioning.statistics import (
    WeightedObservation,
    blend_with_broader,
    effective_sample_size,
    normalize_weights,
    shrinkage_weight,
    weighted_median,
    weighted_quantile,
)


def observation(value: str, weight: str) -> WeightedObservation:
    return WeightedObservation.from_values(value, weight)


def test_weighted_quantiles_sort_values_and_use_first_cumulative_value() -> None:
    values = (
        observation("1400", "0.1"),
        observation("1000", "0.6"),
        observation("1200", "0.3"),
    )

    assert weighted_quantile(values, "0.25") == Decimal("1000")
    assert weighted_median(values) == Decimal("1000")
    assert weighted_quantile(values, "0.75") == Decimal("1200")


def test_statistics_ignore_zero_weights_and_handle_empty_single_and_duplicate_values() -> None:
    assert weighted_median(()) is None
    assert weighted_quantile((observation("1000", "0"),), "0.5") is None
    assert weighted_median((observation("1234.56", "2"),)) == Decimal("1234.56")
    assert weighted_quantile(
        (observation("1000", "1"), observation("1000", "3"), observation("1400", "1")),
        "0.8",
    ) == Decimal("1000")


def test_weights_normalize_exactly_and_preserve_empty_or_all_zero_shape() -> None:
    assert normalize_weights(()) == ()
    assert normalize_weights(("0", Decimal("0"))) == (Decimal("0"), Decimal("0"))
    assert normalize_weights(("2", "3")) == (Decimal("0.4"), Decimal("0.6"))
    assert sum(normalize_weights(("1", "2", "7"))) == Decimal("1")
    assert sum(normalize_weights(("1", "1", "1"))) == Decimal("1")


def test_effective_sample_size_has_expected_equal_and_dominant_weight_behavior() -> None:
    assert effective_sample_size(()) == Decimal("0")
    assert effective_sample_size(("0", "0")) == Decimal("0")
    assert effective_sample_size(("1", "1", "1", "1")) == Decimal("4")
    assert effective_sample_size(("9", "1")) == Decimal("1.219512195121951219512195122")


def test_shrinkage_and_blending_have_explicit_missing_baseline_behavior() -> None:
    assert shrinkage_weight("2", "6") == Decimal("0.25")
    assert blend_with_broader(
        local_estimate=Decimal("1100"),
        broader_estimate=Decimal("1200"),
        local_effective_sample_size=Decimal("2"),
        strength=Decimal("6"),
    ).estimate == Decimal("1175")
    assert blend_with_broader(
        local_estimate=None,
        broader_estimate=Decimal("1200"),
        local_effective_sample_size=Decimal("0"),
    ).local_weight == Decimal("0")
    assert blend_with_broader(
        local_estimate=Decimal("1100"),
        broader_estimate=None,
        local_effective_sample_size=Decimal("2"),
    ).local_weight == Decimal("1")
    assert blend_with_broader(None, None, Decimal("0")) is None


@pytest.mark.parametrize(
    ("weights", "expected"),
    [(("1", "1", "1"), Decimal("3")), (("0", "1", "0"), Decimal("1"))],
)
def test_effective_sample_size_is_scale_invariant(
    weights: tuple[str, ...], expected: Decimal
) -> None:
    assert effective_sample_size(weights) == expected
    doubled = tuple(str(Decimal(weight) * 2) for weight in weights)
    assert effective_sample_size(doubled) == expected


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity"])
def test_weights_reject_negative_or_nonfinite_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_weights((value,))


def test_float_inputs_are_rejected_to_preserve_exact_decimal_contract() -> None:
    with pytest.raises(TypeError):
        WeightedObservation.from_values(1000.0, "1")
    with pytest.raises(TypeError):
        weighted_quantile((observation("1000", "1"),), 0.5)
