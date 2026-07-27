from datetime import timedelta
from decimal import Decimal

import pytest

from carrier_pool.domain.types import (
    CANONICAL_CURRENCY,
    CANONICAL_DISTANCE_UNIT,
    CANONICAL_TIMEZONE,
    CANONICAL_WEIGHT_UNIT,
    EquipmentType,
    LoadStatus,
    Money,
    SourceSystem,
    StopRole,
    UnsupportedSourceStatusError,
    decimal_from_value,
    normalize_external_id,
)


def test_canonical_enums_cover_supported_domain_vocabulary() -> None:
    assert set(SourceSystem) == {
        SourceSystem.FREIGHTFLOW,
        SourceSystem.HAULDESK,
        SourceSystem.BROKEROS,
    }
    assert set(LoadStatus) == {
        LoadStatus.PLANNED,
        LoadStatus.ACTIVE,
        LoadStatus.COVERED,
        LoadStatus.IN_TRANSIT,
        LoadStatus.DELIVERED,
        LoadStatus.COMPLETED,
    }
    assert EquipmentType.UNKNOWN is not EquipmentType.DRY_VAN
    assert StopRole.PICKUP_AND_DROPOFF in StopRole


def test_canonical_units_and_timezone_are_explicit() -> None:
    assert CANONICAL_CURRENCY == "USD"
    assert CANONICAL_WEIGHT_UNIT == "lb"
    assert CANONICAL_DISTANCE_UNIT == "mi"
    assert CANONICAL_TIMEZONE.utcoffset(None) == timedelta(0)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (127472397, "127472397"),
        ("HD-2026-004417", "HD-2026-004417"),
        ("a0jO900000YgsYJIAZ", "a0jO900000YgsYJIAZ"),
    ],
)
def test_normalize_external_id_returns_strings(raw_value: str | int, expected: str) -> None:
    normalized = normalize_external_id(raw_value)

    assert normalized == expected
    assert isinstance(normalized, str)


@pytest.mark.parametrize("raw_value", ["", "   "])
def test_normalize_external_id_rejects_empty_values(raw_value: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_external_id(raw_value)


def test_money_uses_exact_decimal_and_explicit_currency() -> None:
    money = Money.from_value("1450.00")

    assert money.amount == Decimal("1450.00")
    assert money.currency == "USD"
    assert isinstance(decimal_from_value(1035), Decimal)


def test_money_rejects_binary_float_and_non_uppercase_currency() -> None:
    with pytest.raises(TypeError, match="floats are not accepted"):
        decimal_from_value(1450.0)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="three-letter uppercase"):
        Money(amount=Decimal("1.00"), currency="usd")


def test_unknown_source_status_has_a_precise_error() -> None:
    error = UnsupportedSourceStatusError(SourceSystem.FREIGHTFLOW, "Cancelled")

    assert str(error) == (
        "Unsupported FREIGHTFLOW status: 'Cancelled'. "
        "Add an explicit mapping or source-specific warning policy."
    )
