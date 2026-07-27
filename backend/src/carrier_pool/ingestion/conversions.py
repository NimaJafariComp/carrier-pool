"""Pure timestamp and unit conversions used by source normalization.

Canonical timestamps are UTC-aware datetimes. Unit conversions never round: they
return the full Decimal product so presentation and persistence can apply their own
explicit rounding policy later.
"""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from carrier_pool.domain.types import decimal_from_value

HAULDESK_TIMEZONE = ZoneInfo("America/Chicago")
KILOGRAMS_TO_POUNDS = Decimal("2.20462262185")
KILOMETERS_TO_MILES = Decimal("0.621371192237")


def parse_freightflow_datetime(value: str) -> datetime:
    """Parse FreightFlow's offset-aware ISO-8601 timestamp into UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Invalid FreightFlow timestamp: {value!r}") from error

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("FreightFlow timestamps must include a UTC offset.")
    return parsed.astimezone(UTC)


def parse_brokeros_datetime(value: str) -> datetime:
    """Parse BrokerOS's documented UTC timestamp format into UTC."""
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError as error:
        raise ValueError(f"Invalid BrokerOS timestamp: {value!r}") from error

    if parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError("BrokerOS timestamps must use the documented UTC offset +0000.")
    return parsed.astimezone(UTC)


def parse_hauldesk_datetime(value: str) -> datetime:
    """Interpret HaulDesk's naive Central time using DST-aware IANA zone rules."""
    try:
        local_time = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as error:
        raise ValueError(f"Invalid HaulDesk timestamp: {value!r}") from error

    localized = local_time.replace(tzinfo=HAULDESK_TIMEZONE)
    if localized.replace(fold=0).utcoffset() != localized.replace(fold=1).utcoffset():
        raise ValueError("HaulDesk timestamp is ambiguous during daylight-saving transition.")

    round_tripped = localized.astimezone(UTC).astimezone(HAULDESK_TIMEZONE).replace(tzinfo=None)
    if round_tripped != local_time:
        raise ValueError("HaulDesk timestamp does not exist during daylight-saving transition.")

    return localized.astimezone(UTC)


def kilograms_to_pounds(value: object) -> Decimal:
    """Convert kilograms to unrounded canonical pounds using Decimal arithmetic."""
    kilograms = decimal_from_value(value)
    if kilograms < 0:
        raise ValueError("Weight must not be negative.")
    return kilograms * KILOGRAMS_TO_POUNDS


def kilometers_to_miles(value: object) -> Decimal:
    """Convert kilometers to unrounded canonical miles using Decimal arithmetic."""
    kilometers = decimal_from_value(value)
    if kilometers < 0:
        raise ValueError("Distance must not be negative.")
    return kilometers * KILOMETERS_TO_MILES
