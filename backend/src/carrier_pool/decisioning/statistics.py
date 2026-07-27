"""Exact, database-free weighted statistics for rate estimation."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from carrier_pool.domain.types import DecimalInput, decimal_from_value


@dataclass(frozen=True, slots=True)
class WeightedObservation:
    """One exact numeric value with a non-negative evidence weight."""

    value: Decimal
    weight: Decimal

    def __post_init__(self) -> None:
        if not self.value.is_finite():
            raise ValueError("Observation values must be finite.")
        if not self.weight.is_finite() or self.weight < 0:
            raise ValueError("Observation weights must be finite and non-negative.")

    @classmethod
    def from_values(cls, value: DecimalInput, weight: DecimalInput) -> "WeightedObservation":
        """Build an observation without accepting imprecise binary floats."""
        return cls(value=decimal_from_value(value), weight=decimal_from_value(weight))


@dataclass(frozen=True, slots=True)
class BlendedEstimate:
    """A local estimate blended toward an explicitly available broader baseline."""

    estimate: Decimal
    local_weight: Decimal
    local_estimate: Decimal | None
    broader_estimate: Decimal | None
    local_effective_sample_size: Decimal
    strength: Decimal


def normalize_weights(weights: Sequence[DecimalInput]) -> tuple[Decimal, ...]:
    """Return weights summing to one; empty/all-zero inputs preserve zero shape."""
    values = tuple(_non_negative_decimal(weight, "Weights") for weight in weights)
    total = sum(values, Decimal(0))
    if total == 0:
        return values
    normalized_prefix = tuple(weight / total for weight in values[:-1])
    return (*normalized_prefix, Decimal(1) - sum(normalized_prefix, Decimal(0)))


def effective_sample_size(weights: Sequence[DecimalInput]) -> Decimal:
    """Return Kish ESS; empty or all-zero evidence has an ESS of zero."""
    values = tuple(_non_negative_decimal(weight, "Weights") for weight in weights)
    total = sum(values, Decimal(0))
    if total == 0:
        return Decimal(0)
    return (total * total) / sum((weight * weight for weight in values), Decimal(0))


def weighted_quantile(
    observations: Sequence[WeightedObservation], quantile: DecimalInput
) -> Decimal | None:
    """Return first value whose positive cumulative weight reaches ``quantile``.

    Empty and all-zero evidence return ``None``. Duplicate values remain valid and
    zero-weight observations do not alter the result.
    """
    requested_quantile = decimal_from_value(quantile)
    if not Decimal(0) <= requested_quantile <= Decimal(1):
        raise ValueError("Quantile must be between zero and one, inclusive.")

    positive = sorted(
        (observation for observation in observations if observation.weight > 0),
        key=lambda observation: observation.value,
    )
    total = sum((observation.weight for observation in positive), Decimal(0))
    if total == 0:
        return None

    threshold = requested_quantile * total
    cumulative = Decimal(0)
    for observation in positive:
        cumulative += observation.weight
        if cumulative >= threshold:
            return observation.value

    raise AssertionError("Positive cumulative weight did not reach its total.")


def weighted_median(observations: Sequence[WeightedObservation]) -> Decimal | None:
    """Return weighted quantile 0.5 with documented empty-evidence behavior."""
    return weighted_quantile(observations, Decimal("0.5"))


def shrinkage_weight(
    effective_sample_size_value: DecimalInput, strength: DecimalInput = Decimal(6)
) -> Decimal:
    """Return local evidence share ``ESS / (ESS + strength)``."""
    effective = _non_negative_decimal(effective_sample_size_value, "Effective sample size")
    prior_strength = decimal_from_value(strength)
    if prior_strength <= 0:
        raise ValueError("Shrinkage strength must be finite and greater than zero.")
    return effective / (effective + prior_strength)


def blend_with_broader(
    local_estimate: DecimalInput | None,
    broader_estimate: DecimalInput | None,
    local_effective_sample_size: DecimalInput,
    strength: DecimalInput = Decimal(6),
) -> BlendedEstimate | None:
    """Blend local evidence with broader evidence without inventing a prior.

    If only one estimate exists, return it unchanged with full weight for that
    available level. If neither exists, return ``None``.
    """
    local = decimal_from_value(local_estimate) if local_estimate is not None else None
    broader = decimal_from_value(broader_estimate) if broader_estimate is not None else None
    effective = _non_negative_decimal(local_effective_sample_size, "Effective sample size")
    prior_strength = decimal_from_value(strength)
    if prior_strength <= 0:
        raise ValueError("Shrinkage strength must be finite and greater than zero.")
    if local is None and broader is None:
        return None
    if local is None:
        assert broader is not None
        return BlendedEstimate(broader, Decimal(0), None, broader, effective, prior_strength)
    if broader is None:
        return BlendedEstimate(local, Decimal(1), local, None, effective, prior_strength)

    local_weight = shrinkage_weight(effective, prior_strength)
    return BlendedEstimate(
        estimate=(local_weight * local) + ((Decimal(1) - local_weight) * broader),
        local_weight=local_weight,
        local_estimate=local,
        broader_estimate=broader,
        local_effective_sample_size=effective,
        strength=prior_strength,
    )


def _non_negative_decimal(value: DecimalInput, label: str) -> Decimal:
    decimal_value = decimal_from_value(value)
    if decimal_value < 0:
        raise ValueError(f"{label} must be non-negative.")
    return decimal_value
