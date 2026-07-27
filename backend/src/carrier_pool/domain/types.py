"""Canonical primitive types shared by all source adapters."""

from dataclasses import dataclass
from datetime import UTC, tzinfo
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import NewType

CANONICAL_CURRENCY = "USD"
CANONICAL_WEIGHT_UNIT = "lb"
CANONICAL_DISTANCE_UNIT = "mi"
CANONICAL_TIMEZONE: tzinfo = UTC

ExternalId = NewType("ExternalId", str)
type ExternalIdInput = str | int
type DecimalInput = Decimal | int | str


class SourceSystem(StrEnum):
    """Source systems supported by the canonical ingestion boundary."""

    FREIGHTFLOW = "FREIGHTFLOW"
    HAULDESK = "HAULDESK"
    BROKEROS = "BROKEROS"


class LoadStatus(StrEnum):
    """Canonical lifecycle states for a freight load."""

    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COVERED = "COVERED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"


class EquipmentType(StrEnum):
    """Canonical equipment categories, including deliberately unknown equipment."""

    DRY_VAN = "DRY_VAN"
    REEFER = "REEFER"
    FLATBED = "FLATBED"
    UNKNOWN = "UNKNOWN"


class StopRole(StrEnum):
    """A stop's source-independent pickup and drop-off role."""

    PICKUP = "PICKUP"
    DROPOFF = "DROPOFF"
    PICKUP_AND_DROPOFF = "PICKUP_AND_DROPOFF"
    UNSPECIFIED = "UNSPECIFIED"


class FinancialSide(StrEnum):
    """The broker-facing side of a source financial entry."""

    BILL = "BILL"
    PAY = "PAY"


class UnsupportedSourceStatusError(ValueError):
    """Raised when an adapter encounters an undocumented source status value."""

    def __init__(self, source_system: SourceSystem, source_value: object) -> None:
        super().__init__(
            f"Unsupported {source_system.value} status: {source_value!r}. "
            "Add an explicit mapping or source-specific warning policy."
        )


def normalize_external_id(value: ExternalIdInput) -> ExternalId:
    """Return a non-empty canonical string identifier without changing its semantics."""
    if isinstance(value, bool):
        raise TypeError("External IDs must be strings or integers, not booleans.")

    normalized = str(value).strip()
    if not normalized:
        raise ValueError("External IDs must not be empty.")

    return ExternalId(normalized)


def decimal_from_value(value: object) -> Decimal:
    """Convert exact source values to Decimal while rejecting binary floats."""
    if isinstance(value, bool):
        raise TypeError("Money values must not be booleans.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(f"Invalid decimal value: {value!r}") from error
    else:
        raise TypeError(
            "Money values must be Decimal, integer, or string; floats are not accepted."
        )

    if not result.is_finite():
        raise ValueError("Money values must be finite.")

    return result


@dataclass(frozen=True, slots=True)
class Money:
    """An exact monetary amount with an explicit ISO 4217 currency code."""

    amount: Decimal
    currency: str = CANONICAL_CURRENCY

    def __post_init__(self) -> None:
        if not self.amount.is_finite():
            raise ValueError("Money.amount must be finite.")
        if len(self.currency) != 3 or self.currency != self.currency.upper():
            raise ValueError("Money.currency must be a three-letter uppercase ISO 4217 code.")

    @classmethod
    def from_value(cls, amount: DecimalInput, currency: str = CANONICAL_CURRENCY) -> "Money":
        """Construct Money from an exact input representation."""
        return cls(amount=decimal_from_value(amount), currency=currency)
