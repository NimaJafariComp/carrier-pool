"""Typed, source-independent inputs for deterministic scenario generation."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from random import Random

from carrier_pool.domain.types import EquipmentType, FinancialSide, LoadStatus, Money, SourceSystem


class CarrierHistoryProfile(StrEnum):
    """Intentional history shape used by later ranking scenarios."""

    STANDARD = "STANDARD"
    RICH_LANE = "RICH_LANE"
    LOW_HISTORY = "LOW_HISTORY"
    BROAD_EQUIPMENT_POOR_LANE = "BROAD_EQUIPMENT_POOR_LANE"
    RECENT_DELIVERY = "RECENT_DELIVERY"
    STALE_DELIVERY = "STALE_DELIVERY"


def _required(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty.")
    return value


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    """Fixed seed configuration; only background variation may consume it."""

    seed: int = 20260701

    def minor_variation(self, key: str, lower: int, upper: int) -> int:
        """Return stable minor variation without influencing core scenario facts."""
        if lower > upper:
            raise ValueError("lower must not be greater than upper.")
        return Random(f"{self.seed}:{key}").randint(lower, upper)


@dataclass(frozen=True, slots=True)
class GeneratorTenant:
    tenant_id: str
    name: str
    source_system: SourceSystem

    def __post_init__(self) -> None:
        _required(self.tenant_id, "tenant_id")
        _required(self.name, "name")


@dataclass(frozen=True, slots=True)
class LocationDefinition:
    location_id: str
    city: str
    state: str
    postal_code: str

    def __post_init__(self) -> None:
        _required(self.location_id, "location_id")
        _required(self.city, "city")
        if len(self.state) != 2:
            raise ValueError("state must be a two-letter abbreviation.")
        _required(self.postal_code, "postal_code")


@dataclass(frozen=True, slots=True)
class CustomerDefinition:
    customer_id: str
    tenant_id: str
    name: str

    def __post_init__(self) -> None:
        _required(self.customer_id, "customer_id")
        _required(self.tenant_id, "tenant_id")
        _required(self.name, "name")


@dataclass(frozen=True, slots=True)
class CarrierDefinition:
    carrier_id: str
    tenant_id: str
    name: str
    mc_number: str | None = None
    dot_number: str | None = None
    equipment_history: tuple[EquipmentType, ...] = ()
    history_profile: CarrierHistoryProfile = CarrierHistoryProfile.STANDARD
    last_delivery_location_id: str | None = None
    last_delivery_date: date | None = None

    def __post_init__(self) -> None:
        _required(self.carrier_id, "carrier_id")
        _required(self.tenant_id, "tenant_id")
        _required(self.name, "name")
        if self.last_delivery_location_id is not None:
            _required(self.last_delivery_location_id, "last_delivery_location_id")
        if (self.last_delivery_location_id is None) != (self.last_delivery_date is None):
            raise ValueError("last delivery location and date must be supplied together.")


@dataclass(frozen=True, slots=True)
class ScenarioStop:
    """A route stop; postal code may be overridden by a later correction."""

    sequence: int
    is_pickup: bool
    is_dropoff: bool
    location_id: str
    planned_date: date
    postal_code: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("stop sequence must be positive.")
        _required(self.location_id, "location_id")
        if self.postal_code is not None:
            _required(self.postal_code, "postal_code")


@dataclass(frozen=True, slots=True)
class GeneratorLoad:
    logical_id: str
    tenant_id: str
    source_system: SourceSystem
    customer_id: str
    stops: tuple[ScenarioStop, ...]
    equipment: EquipmentType
    distance_miles: Decimal
    day11_target: bool = False
    evaluation_probe: bool = False
    history_anchor: bool = False

    def __post_init__(self) -> None:
        _required(self.logical_id, "logical_id")
        _required(self.tenant_id, "tenant_id")
        _required(self.customer_id, "customer_id")
        if not self.stops:
            raise ValueError("loads require at least one stop.")
        if self.distance_miles <= 0:
            raise ValueError("load distance_miles must be positive.")
        sequences = tuple(stop.sequence for stop in self.stops)
        if sequences != tuple(sorted(sequences)) or len(set(sequences)) != len(sequences):
            raise ValueError("load stops must have unique ordered sequences.")


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """A replacement-snapshot lifecycle event or field correction."""

    load_id: str
    occurred_at: datetime
    status: LoadStatus | None = None
    carrier_id: str | None = None
    customer_rate: Money | None = None
    carrier_rate: Money | None = None
    equipment: EquipmentType | None = None
    stops: tuple[ScenarioStop, ...] | None = None
    correction_reason: str | None = None

    def __post_init__(self) -> None:
        _required(self.load_id, "load_id")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware.")
        if (
            self.status is None
            and self.carrier_id is None
            and self.customer_rate is None
            and self.carrier_rate is None
            and self.equipment is None
            and self.stops is None
        ):
            raise ValueError("lifecycle event must change a field.")
        if self.correction_reason is not None:
            _required(self.correction_reason, "correction_reason")


@dataclass(frozen=True, slots=True)
class FinancialEvent:
    """One append-only source financial entry."""

    load_id: str
    occurred_at: datetime
    entry_id: str
    side: FinancialSide
    code: str
    amount: Money

    def __post_init__(self) -> None:
        _required(self.load_id, "load_id")
        _required(self.entry_id, "entry_id")
        _required(self.code, "code")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware.")


ScheduledEvent = LifecycleEvent | FinancialEvent


@dataclass(frozen=True, slots=True)
class ScheduledSync:
    """One source file slot, before any source-specific serialization."""

    sync_id: str
    tenant_id: str
    source_system: SourceSystem
    sync_at: datetime
    events: tuple[ScheduledEvent, ...]

    def __post_init__(self) -> None:
        _required(self.sync_id, "sync_id")
        _required(self.tenant_id, "tenant_id")
        if self.sync_at.tzinfo is None or self.sync_at.utcoffset() is None:
            raise ValueError("sync_at must be timezone-aware.")
        if not self.events:
            raise ValueError("scheduled syncs require at least one event.")
        if any(event.occurred_at > self.sync_at for event in self.events):
            raise ValueError("event occurred_at must not be after sync_at.")
        if not 1 <= len({event.load_id for event in self.events}) <= 3:
            raise ValueError("scheduled syncs must contain one to three changed loads.")


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """Hand-authored required scenario metadata; manifest output is derived from this."""

    scenario_id: str
    load_ids: tuple[str, ...]
    carrier_ids: tuple[str, ...]
    description: str
    expected_effect: str
    verification_test: str
    expected_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.scenario_id, "scenario_id")
        _required(self.description, "description")
        _required(self.expected_effect, "expected_effect")
        _required(self.verification_test, "verification_test")
        if not self.load_ids and not self.carrier_ids:
            raise ValueError("scenario requires a load or carrier reference.")


@dataclass(frozen=True, slots=True)
class RankingHoldoutDefinition:
    """Hand-authored later booking label and required evaluation coverage."""

    load_id: str
    booked_carrier_id: str
    coverage_tags: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(self.load_id, "load_id")
        _required(self.booked_carrier_id, "booked_carrier_id")
        if not self.coverage_tags:
            raise ValueError("ranking holdout requires coverage tags.")


@dataclass(frozen=True, slots=True)
class ScenarioCatalog:
    """Hand-authored scenario identities and expected behavior inputs."""

    tenants: tuple[GeneratorTenant, ...]
    locations: tuple[LocationDefinition, ...]
    customers: tuple[CustomerDefinition, ...]
    carriers: tuple[CarrierDefinition, ...]
    loads: tuple[GeneratorLoad, ...]
    scenarios: tuple[ScenarioDefinition, ...] = ()
    ranking_holdouts: tuple[RankingHoldoutDefinition, ...] = ()

    def __post_init__(self) -> None:
        tenant_ids = _unique_ids(self.tenants, "tenant_id", "tenant")
        location_ids = _unique_ids(self.locations, "location_id", "location")
        customer_ids = _unique_ids(self.customers, "customer_id", "customer")
        _unique_ids(self.carriers, "carrier_id", "carrier")
        _unique_ids(self.loads, "logical_id", "load")
        _unique_ids(self.scenarios, "scenario_id", "scenario")
        _unique_ids(self.ranking_holdouts, "load_id", "ranking holdout")
        if not tenant_ids:
            raise ValueError("catalog requires a tenant.")
        for customer in self.customers:
            if customer.tenant_id not in tenant_ids:
                raise ValueError(f"unknown customer tenant: {customer.tenant_id}")
        for carrier in self.carriers:
            if carrier.tenant_id not in tenant_ids:
                raise ValueError(f"unknown carrier tenant: {carrier.tenant_id}")
            if (
                carrier.last_delivery_location_id is not None
                and carrier.last_delivery_location_id not in location_ids
            ):
                raise ValueError(
                    f"unknown carrier delivery location: {carrier.last_delivery_location_id}"
                )
        for load in self.loads:
            if load.tenant_id not in tenant_ids:
                raise ValueError(f"unknown load tenant: {load.tenant_id}")
            if load.customer_id not in customer_ids:
                raise ValueError(f"unknown load customer: {load.customer_id}")
            customer = next(
                customer for customer in self.customers if customer.customer_id == load.customer_id
            )
            if customer.tenant_id != load.tenant_id:
                raise ValueError("load customer must belong to the load tenant.")
            if any(stop.location_id not in location_ids for stop in load.stops):
                raise ValueError(f"load {load.logical_id} references an unknown location.")
        load_ids = {load.logical_id for load in self.loads}
        carrier_ids = {carrier.carrier_id for carrier in self.carriers}
        for scenario in self.scenarios:
            if any(load_id not in load_ids for load_id in scenario.load_ids):
                raise ValueError(f"scenario {scenario.scenario_id} references an unknown load.")
            if any(carrier_id not in carrier_ids for carrier_id in scenario.carrier_ids):
                raise ValueError(f"scenario {scenario.scenario_id} references an unknown carrier.")
        for holdout in self.ranking_holdouts:
            if holdout.load_id not in load_ids or holdout.booked_carrier_id not in carrier_ids:
                raise ValueError("ranking holdout references an unknown load or carrier.")
            if (
                self.load(holdout.load_id).tenant_id
                != self.carrier(holdout.booked_carrier_id).tenant_id
            ):
                raise ValueError("ranking holdout carrier must belong to the load tenant.")

    def load(self, logical_id: str) -> GeneratorLoad:
        return next(load for load in self.loads if load.logical_id == logical_id)

    def location(self, location_id: str) -> LocationDefinition:
        return next(location for location in self.locations if location.location_id == location_id)

    def carrier(self, carrier_id: str) -> CarrierDefinition:
        return next(carrier for carrier in self.carriers if carrier.carrier_id == carrier_id)


def _unique_ids(items: tuple[object, ...], attribute: str, label: str) -> set[str]:
    values = [getattr(item, attribute) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} ID.")
    return set(values)
