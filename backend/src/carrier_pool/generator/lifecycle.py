"""Deterministic reducer for hand-authored scenario lifecycle events."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from carrier_pool.domain.types import EquipmentType, LoadStatus, Money
from carrier_pool.generator.models import (
    FinancialEvent,
    GeneratorConfig,
    LifecycleEvent,
    ScenarioCatalog,
    ScenarioStop,
    ScheduledSync,
)


@dataclass(frozen=True, slots=True)
class LoadLifecycleState:
    logical_id: str
    status: LoadStatus | None
    carrier_id: str | None
    customer_rate: Money | None
    carrier_rate: Money | None
    equipment: EquipmentType
    stops: tuple[ScenarioStop, ...]
    financial_entries: tuple[FinancialEvent, ...]
    applied_event_ids: tuple[str, ...]
    day11_activated_at: datetime | None


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    loads: Mapping[str, LoadLifecycleState]


class LifecycleEngine:
    """Apply explicit events chronologically without assuming status monotonicity."""

    def __init__(self, catalog: ScenarioCatalog, config: GeneratorConfig | None = None) -> None:
        self._catalog = catalog
        self._config = config or GeneratorConfig()

    def apply(self, scheduled_syncs: tuple[ScheduledSync, ...]) -> LifecycleResult:
        states = {
            load.logical_id: self._initial_state(load.logical_id) for load in self._catalog.loads
        }
        ledger_entry_ids: set[str] = set()

        for sync in sorted(scheduled_syncs, key=lambda item: (item.sync_at, item.sync_id)):
            for index, event in enumerate(sync.events):
                load = self._catalog.load(event.load_id)
                self._validate_sync_binding(sync, load.tenant_id, load.source_system)
                event_id = f"{sync.sync_id}:{index}"
                if isinstance(event, LifecycleEvent):
                    self._validate_lifecycle_event(event, load.tenant_id)
                    states[event.load_id] = self._apply_lifecycle_event(
                        states[event.load_id], event, event_id, load.day11_target
                    )
                else:
                    if event.entry_id in ledger_entry_ids:
                        raise ValueError(f"duplicate financial entry ID: {event.entry_id}")
                    ledger_entry_ids.add(event.entry_id)
                    states[event.load_id] = self._apply_financial_event(
                        states[event.load_id], event, event_id, load.day11_target
                    )

        return LifecycleResult(MappingProxyType(states))

    def _initial_state(self, load_id: str) -> LoadLifecycleState:
        load = self._catalog.load(load_id)
        stops = tuple(
            ScenarioStop(
                sequence=stop.sequence,
                is_pickup=stop.is_pickup,
                is_dropoff=stop.is_dropoff,
                location_id=stop.location_id,
                planned_date=stop.planned_date,
                postal_code=(
                    stop.postal_code or self._catalog.location(stop.location_id).postal_code
                ),
            )
            for stop in load.stops
        )
        return LoadLifecycleState(
            logical_id=load.logical_id,
            status=None,
            carrier_id=None,
            customer_rate=None,
            carrier_rate=None,
            equipment=load.equipment,
            stops=stops,
            financial_entries=(),
            applied_event_ids=(),
            day11_activated_at=None,
        )

    @staticmethod
    def _validate_sync_binding(sync: ScheduledSync, tenant_id: str, source_system: object) -> None:
        if sync.tenant_id != tenant_id or sync.source_system is not source_system:
            raise ValueError("scheduled sync tenant/source does not match its load.")

    def _validate_lifecycle_event(self, event: LifecycleEvent, tenant_id: str) -> None:
        if (
            event.carrier_id is not None
            and self._catalog.carrier(event.carrier_id).tenant_id != tenant_id
        ):
            raise ValueError("assigned carrier must belong to the load tenant.")
        if event.stops is not None:
            for stop in event.stops:
                self._catalog.location(stop.location_id)

    @staticmethod
    def _apply_lifecycle_event(
        state: LoadLifecycleState,
        event: LifecycleEvent,
        event_id: str,
        day11_target: bool,
    ) -> LoadLifecycleState:
        if (
            day11_target
            and state.day11_activated_at is not None
            and event.status is not LoadStatus.ACTIVE
        ):
            raise ValueError("Day 11 target cannot progress after its active decision input.")
        activated_at = (
            event.occurred_at
            if day11_target and event.status is LoadStatus.ACTIVE
            else state.day11_activated_at
        )
        return LoadLifecycleState(
            logical_id=state.logical_id,
            status=event.status if event.status is not None else state.status,
            carrier_id=event.carrier_id if event.carrier_id is not None else state.carrier_id,
            customer_rate=event.customer_rate
            if event.customer_rate is not None
            else state.customer_rate,
            carrier_rate=(
                event.carrier_rate if event.carrier_rate is not None else state.carrier_rate
            ),
            equipment=event.equipment if event.equipment is not None else state.equipment,
            stops=event.stops if event.stops is not None else state.stops,
            financial_entries=state.financial_entries,
            applied_event_ids=(*state.applied_event_ids, event_id),
            day11_activated_at=activated_at,
        )

    @staticmethod
    def _apply_financial_event(
        state: LoadLifecycleState,
        event: FinancialEvent,
        event_id: str,
        day11_target: bool,
    ) -> LoadLifecycleState:
        if day11_target and state.day11_activated_at is not None:
            raise ValueError("Day 11 target cannot receive a future financial event.")
        return LoadLifecycleState(
            logical_id=state.logical_id,
            status=state.status,
            carrier_id=state.carrier_id,
            customer_rate=state.customer_rate,
            carrier_rate=state.carrier_rate,
            equipment=state.equipment,
            stops=state.stops,
            financial_entries=(*state.financial_entries, event),
            applied_event_ids=(*state.applied_event_ids, event_id),
            day11_activated_at=state.day11_activated_at,
        )
