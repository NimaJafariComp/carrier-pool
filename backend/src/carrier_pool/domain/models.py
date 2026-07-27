"""Immutable source-independent snapshots produced by normalization."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from carrier_pool.domain.types import (
    EquipmentType,
    ExternalId,
    FinancialSide,
    LoadStatus,
    Money,
    SourceSystem,
    normalize_external_id,
)


def _require_utc_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC.")
    if value.tzinfo is not UTC:
        raise ValueError(f"{field_name} must use UTC timezone.")


@dataclass(frozen=True, slots=True)
class CanonicalSourceIdentity:
    """A source identifier, namespaced by its tenant and TMS."""

    tenant_id: str
    source_system: SourceSystem
    external_id: ExternalId

    def __post_init__(self) -> None:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id must not be empty.")
        object.__setattr__(self, "external_id", normalize_external_id(self.external_id))


@dataclass(frozen=True, slots=True)
class CanonicalCustomerSnapshot:
    """A tenant-scoped customer representation reported by a source."""

    identity: CanonicalSourceIdentity
    name: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Customer name must not be empty.")


@dataclass(frozen=True, slots=True)
class CanonicalCarrierSnapshot:
    """A tenant-scoped carrier representation reported by a source."""

    identity: CanonicalSourceIdentity
    name: str
    mc_number: str | None = None
    dot_number: str | None = None
    phone_number: str | None = None
    home_city: str | None = None
    home_state: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Carrier name must not be empty.")


@dataclass(frozen=True, slots=True)
class CanonicalStop:
    """An ordered route stop with independent pickup and drop-off flags."""

    sequence: int
    is_pickup: bool
    is_dropoff: bool
    city: str
    state: str
    postal_code: str
    facility_name: str | None = None
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    actual_arrival_at: datetime | None = None
    actual_departure_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("Stop sequence must be at least 1.")
        if not self.city.strip() or not self.state.strip() or not self.postal_code.strip():
            raise ValueError("Stop city, state, and postal_code must not be empty.")

        for field_name in (
            "scheduled_start_at",
            "scheduled_end_at",
            "actual_arrival_at",
            "actual_departure_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_utc_datetime(value, field_name)

        if (
            self.scheduled_start_at is not None
            and self.scheduled_end_at is not None
            and self.scheduled_end_at < self.scheduled_start_at
        ):
            raise ValueError("scheduled_end_at must not be before scheduled_start_at.")


@dataclass(frozen=True, slots=True)
class SyncMetadata:
    """Timing and provenance for one source sync file."""

    tenant_id: str
    source_system: SourceSystem
    source_file_name: str
    sync_at: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.source_file_name.strip():
            raise ValueError("tenant_id and source_file_name must not be empty.")
        _require_utc_datetime(self.sync_at, "sync_at")
        _require_utc_datetime(self.observed_at, "observed_at")
        if self.observed_at < self.sync_at:
            raise ValueError("observed_at must not be before sync_at.")


@dataclass(frozen=True, slots=True)
class CanonicalLoadSnapshot:
    """The normalized state of one load as reported in one source observation."""

    identity: CanonicalSourceIdentity
    status: LoadStatus
    customer: CanonicalCustomerSnapshot
    stops: tuple[CanonicalStop, ...]
    source_created_at: datetime
    source_modified_at: datetime
    carrier: CanonicalCarrierSnapshot | None = None
    equipment: EquipmentType | None = None
    customer_rate: Money | None = None
    carrier_rate: Money | None = None
    weight_lbs: Decimal | None = None
    distance_miles: Decimal | None = None
    load_number: str | None = None

    def __post_init__(self) -> None:
        _require_utc_datetime(self.source_created_at, "source_created_at")
        _require_utc_datetime(self.source_modified_at, "source_modified_at")
        if self.source_modified_at < self.source_created_at:
            raise ValueError("source_modified_at must not be before source_created_at.")
        if not self.stops:
            raise ValueError("A load snapshot must contain at least one stop.")
        sequences = tuple(stop.sequence for stop in self.stops)
        if sequences != tuple(sorted(sequences)) or len(set(sequences)) != len(sequences):
            raise ValueError("Load stops must have unique, ordered sequences.")
        if self.weight_lbs is not None and self.weight_lbs < 0:
            raise ValueError("weight_lbs must not be negative.")
        if self.distance_miles is not None and self.distance_miles < 0:
            raise ValueError("distance_miles must not be negative.")


@dataclass(frozen=True, slots=True)
class NormalizationWarning:
    """A structured, non-fatal data-quality warning from normalization."""

    code: str
    message: str
    source_identity: CanonicalSourceIdentity | None = None
    field_path: str | None = None

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("Normalization warnings require a code and message.")


@dataclass(frozen=True, slots=True)
class SourceFinancialEntry:
    """One source-reported bill or pay fact, retained independently from load totals."""

    identity: CanonicalSourceIdentity
    load_identity: CanonicalSourceIdentity
    side: FinancialSide
    code: str
    amount: Money
    source_created_at: datetime

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("Source financial entry code must not be empty.")
        if (
            self.identity.tenant_id != self.load_identity.tenant_id
            or self.identity.source_system is not self.load_identity.source_system
        ):
            raise ValueError(
                "Source financial entry and load identities must share a tenant and source."
            )
        _require_utc_datetime(self.source_created_at, "source_created_at")


@dataclass(frozen=True, slots=True)
class NormalizedSync:
    """All source-independent snapshots and warnings emitted from one sync file."""

    metadata: SyncMetadata
    loads: tuple[CanonicalLoadSnapshot, ...] = field(default_factory=tuple)
    customers: tuple[CanonicalCustomerSnapshot, ...] = field(default_factory=tuple)
    carriers: tuple[CanonicalCarrierSnapshot, ...] = field(default_factory=tuple)
    source_financial_entries: tuple[SourceFinancialEntry, ...] = field(default_factory=tuple)
    warnings: tuple[NormalizationWarning, ...] = field(default_factory=tuple)
