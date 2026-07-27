from datetime import UTC, datetime
from decimal import Decimal

import pytest

from carrier_pool.ingestion.conversions import (
    KILOGRAMS_TO_POUNDS,
    KILOMETERS_TO_MILES,
    kilograms_to_pounds,
    kilometers_to_miles,
    parse_brokeros_datetime,
    parse_freightflow_datetime,
    parse_hauldesk_datetime,
)


def test_freightflow_offset_timestamp_becomes_utc() -> None:
    assert parse_freightflow_datetime("2026-07-06T06:00:00-05:00") == datetime(
        2026, 7, 6, 11, tzinfo=UTC
    )


def test_freightflow_rejects_timestamp_without_offset() -> None:
    with pytest.raises(ValueError, match="must include a UTC offset"):
        parse_freightflow_datetime("2026-07-06T06:00:00")


def test_brokeros_utc_timestamp_becomes_utc() -> None:
    assert parse_brokeros_datetime("2026-07-06T11:00:00.000+0000") == datetime(
        2026, 7, 6, 11, tzinfo=UTC
    )


def test_brokeros_rejects_non_utc_offset() -> None:
    with pytest.raises(ValueError, match="documented UTC offset"):
        parse_brokeros_datetime("2026-07-06T06:00:00.000-0500")


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        ("2026-01-15 06:00:00", datetime(2026, 1, 15, 12, tzinfo=UTC)),
        ("2026-07-06 06:00:00", datetime(2026, 7, 6, 11, tzinfo=UTC)),
    ],
)
def test_hauldesk_central_timestamps_follow_dst_rules(
    source_value: str, expected: datetime
) -> None:
    assert parse_hauldesk_datetime(source_value) == expected


@pytest.mark.parametrize("source_value", ["2026-03-08 02:30:00", "2026-11-01 01:30:00"])
def test_hauldesk_rejects_nonexistent_or_ambiguous_dst_times(source_value: str) -> None:
    with pytest.raises(ValueError, match="daylight-saving transition"):
        parse_hauldesk_datetime(source_value)


def test_decimal_unit_conversions_preserve_full_precision_without_rounding() -> None:
    assert kilograms_to_pounds("1") == KILOGRAMS_TO_POUNDS
    assert kilometers_to_miles("1") == KILOMETERS_TO_MILES
    assert kilograms_to_pounds("10886.2") == Decimal("10886.2") * KILOGRAMS_TO_POUNDS
    assert kilometers_to_miles("389.6") == Decimal("389.6") * KILOMETERS_TO_MILES


@pytest.mark.parametrize("converter", [kilograms_to_pounds, kilometers_to_miles])
def test_unit_conversions_reject_binary_floats(converter: object) -> None:
    with pytest.raises(TypeError, match="floats are not accepted"):
        converter(1.5)  # type: ignore[operator]
