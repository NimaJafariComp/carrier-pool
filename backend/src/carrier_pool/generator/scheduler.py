"""Deterministic Phase 6 sync schedule and safe plain-JSON file writer."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from re import compile as re_compile

from carrier_pool.domain.types import FinancialSide, LoadStatus, Money, SourceSystem
from carrier_pool.generator.catalog import build_catalog
from carrier_pool.generator.lifecycle import LifecycleEngine
from carrier_pool.generator.models import (
    FinancialEvent,
    GeneratorLoad,
    LifecycleEvent,
    ScenarioCatalog,
    ScheduledSync,
)
from carrier_pool.generator.serializers import serialize_sync

HISTORICAL_START = date(2026, 7, 1)
HISTORICAL_DAYS = 10
SYNC_HOURS = (0, 6, 12, 18)
HISTORICAL_SYNC_COUNT = HISTORICAL_DAYS * len(SYNC_HOURS) * len(SourceSystem)
DAY11_SYNC_AT = datetime(2026, 7, 11, 6, tzinfo=UTC)

_FILENAME_PATTERN = re_compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}_sync\.json\Z")
_DIRECTORIES = {
    SourceSystem.FREIGHTFLOW: "tms_a_freightflow",
    SourceSystem.HAULDESK: "tms_b_hauldesk",
    SourceSystem.BROKEROS: "tms_c_brokeros",
}
_STATUSES = (
    LoadStatus.PLANNED,
    LoadStatus.ACTIVE,
    LoadStatus.COVERED,
    LoadStatus.IN_TRANSIT,
    LoadStatus.DELIVERED,
    LoadStatus.COMPLETED,
)
ANCHOR_LOAD_IDS = {
    SourceSystem.FREIGHTFLOW: ("FF-0901", "FF-0902", "FF-0903"),
    SourceSystem.HAULDESK: ("HD-1901", "HD-1902", "HD-1903"),
    SourceSystem.BROKEROS: ("BO-2901", "BO-2902", "BO-2903"),
}

# Hand-authored booking and eventually corrected carrier-pay totals. These are
# intentionally not a formula of lifecycle position: they encode lane, equipment,
# time, and exceptional-final-payment variation for leakage-safe rate evaluation.
_RATE_OUTCOMES: dict[str, tuple[Decimal, Decimal]] = {
    "FF-0901": (Decimal("1190"), Decimal("1190")),
    "FF-0902": (Decimal("1215"), Decimal("1215")),
    "FF-0903": (Decimal("1205"), Decimal("1205")),
    "FF-0904": (Decimal("1220"), Decimal("1220")),
    "FF-0905": (Decimal("1200"), Decimal("1200")),
    "FF-0906": (Decimal("1215"), Decimal("1215")),
    "FF-0907": (Decimal("1195"), Decimal("1195")),
    "FF-0908": (Decimal("1230"), Decimal("1230")),
    "FF-0909": (Decimal("1210"), Decimal("1210")),
    "FF-0990": (Decimal("1185"), Decimal("1185")),
    "HD-1901": (Decimal("1260"), Decimal("1260")),
    "HD-1902": (Decimal("1285"), Decimal("1285")),
    "HD-1903": (Decimal("1270"), Decimal("1270")),
    "HD-1904": (Decimal("1240"), Decimal("1240")),
    "HD-1905": (Decimal("1225"), Decimal("1225")),
    "HD-1906": (Decimal("1535"), Decimal("1535")),
    "HD-1907": (Decimal("1490"), Decimal("1490")),
    "HD-1908": (Decimal("1570"), Decimal("1570")),
    "HD-1909": (Decimal("1210"), Decimal("1210")),
    "HD-1910": (Decimal("1230"), Decimal("1230")),
    "BO-2901": (Decimal("1540"), Decimal("1540")),
    "BO-2902": (Decimal("1560"), Decimal("1560")),
    "BO-2903": (Decimal("1555"), Decimal("1555")),
    "BO-2904": (Decimal("1515"), Decimal("1515")),
    "BO-2905": (Decimal("1580"), Decimal("1580")),
    "BO-2906": (Decimal("1545"), Decimal("1545")),
    "BO-2907": (Decimal("1280"), Decimal("1280")),
    "BO-2908": (Decimal("1530"), Decimal("1530")),
    "BO-2909": (Decimal("1265"), Decimal("1265")),
    "BO-2910": (Decimal("1310"), Decimal("1310")),
    # FreightFlow: DFW→Houston dry-van history, reefer premium, and corrections.
    "FF-1001": (Decimal("1180"), Decimal("1180")),
    "FF-1101": (Decimal("1210"), Decimal("1265")),
    "FF-1201": (Decimal("1235"), Decimal("1210")),
    "FF-1301": (Decimal("1660"), Decimal("1695")),
    "FF-1401": (Decimal("1120"), Decimal("1175")),
    "FF-1402": (Decimal("1160"), Decimal("1160")),
    "FF-1501": (Decimal("1240"), Decimal("1260")),
    "FF-1502": (Decimal("1185"), Decimal("1165")),
    "FF-1503": (Decimal("1740"), Decimal("1810")),
    # HaulDesk: final changes become append-only ledger adjustments.
    "HD-2001": (Decimal("1280"), Decimal("1280")),
    "HD-2002": (Decimal("1190"), Decimal("1225")),
    "HD-2003": (Decimal("1250"), Decimal("1250")),
    "HD-2004": (Decimal("1480"), Decimal("1540")),
    "HD-2005": (Decimal("1600"), Decimal("1575")),
    "HD-2101": (Decimal("1150"), Decimal("1150")),
    "HD-2201": (Decimal("1305"), Decimal("1335")),
    "HD-2202": (Decimal("1180"), Decimal("1180")),
    "HD-2203": (Decimal("1540"), Decimal("1500")),
    # BrokerOS: reefer/dry-van and multi-stop outcomes include market variation.
    "BO-3001": (Decimal("1550"), Decimal("1550")),
    "BO-3002": (Decimal("1490"), Decimal("1520")),
    "BO-3003": (Decimal("1630"), Decimal("1660")),
    "BO-3004": (Decimal("1880"), Decimal("1935")),
    "BO-3005": (Decimal("1260"), Decimal("1240")),
    "BO-3101": (Decimal("1510"), Decimal("1510")),
    "BO-3201": (Decimal("1575"), Decimal("1610")),
    "BO-3202": (Decimal("1535"), Decimal("1505")),
    "BO-3203": (Decimal("1300"), Decimal("1340")),
}

_ANCHOR_CARRIERS = {
    "FF-0901": "FF-C-201",
    "FF-0902": "FF-C-201",
    "FF-0903": "FF-C-201",
    "FF-0904": "FF-C-201",
    "FF-0905": "FF-C-201",
    "FF-0906": "FF-C-201",
    # Keep Lone Star's strong exact-lane history, but reserve two anchor slots for
    # genuinely distinct FF candidates.  Catalog carrier diversity must be
    # represented by completed work, not just dormant carrier master records.
    "FF-0907": "FF-C-202",
    "FF-0908": "FF-C-201",
    "FF-0909": "FF-C-201",
    "FF-0990": "FF-C-201",
    "HD-1901": "HD-C-401",
    "HD-1902": "HD-C-401",
    "HD-1903": "HD-C-401",
    "HD-1904": "HD-C-404",
    "HD-1905": "HD-C-405",
    "HD-1906": "HD-C-402",
    "HD-1907": "HD-C-407",
    "HD-1908": "HD-C-403",
    "HD-1909": "HD-C-408",
    "HD-1910": "HD-C-206",
    "BO-2901": "BO-C-601",
    "BO-2902": "BO-C-601",
    "BO-2903": "BO-C-601",
    "BO-2904": "BO-C-602",
    "BO-2905": "BO-C-603",
    "BO-2906": "BO-C-605",
    "BO-2907": "BO-C-606",
    "BO-2908": "BO-C-607",
    "BO-2909": "BO-C-608",
    "BO-2910": "BO-C-604",
}

# Keep the two delivery-proximity examples intentionally distinct: Heritage finishes
# early enough to be stale by Day 11, while Metro finishes late and remains recent.
_ORDINARY_LIFECYCLE_ORDER = {
    SourceSystem.FREIGHTFLOW: (
        "FF-1402",
        "FF-1001",
        "FF-1101",
        "FF-1301",
        "FF-1201",
        "FF-1401",
    ),
}


def build_schedule(catalog: ScenarioCatalog) -> tuple[ScheduledSync, ...]:
    """Create all 120 historical slots and three explicit Day 11 active-load slots."""
    historical_loads: dict[SourceSystem, tuple[GeneratorLoad, ...]] = {
        source: tuple(
            load
            for load in catalog.loads
            if (
                load.source_system is source
                and not load.day11_target
                and not load.evaluation_probe
                and not load.history_anchor
            )
        )
        for source in SourceSystem
    }
    anchor_loads: dict[SourceSystem, tuple[GeneratorLoad, ...]] = {
        source: tuple(
            load for load in catalog.loads if load.source_system is source and load.history_anchor
        )
        for source in SourceSystem
    }
    if any(len(loads) != 6 for loads in historical_loads.values()):
        raise ValueError("catalog requires six ordinary historical loads per source.")
    if any(len(loads) < 3 for loads in anchor_loads.values()):
        raise ValueError("catalog requires at least three history anchors per source.")

    schedule: list[ScheduledSync] = []
    for day_offset in range(HISTORICAL_DAYS):
        sync_date = HISTORICAL_START + timedelta(days=day_offset)
        for hour in SYNC_HOURS:
            sync_at = datetime.combine(sync_date, datetime.min.time(), tzinfo=UTC).replace(
                hour=hour
            )
            slot = day_offset * len(SYNC_HOURS) + SYNC_HOURS.index(hour)
            for source in SourceSystem:
                if slot < len(historical_loads[source]) * len(_STATUSES):
                    events = _historical_events_for_slot(
                        catalog,
                        source,
                        historical_loads[source],
                        anchor_loads[source],
                        sync_at,
                        slot,
                    )
                    schedule.append(
                        ScheduledSync(
                            sync_id=f"{source.value}-{sync_at:%Y%m%dT%H%M}",
                            tenant_id=historical_loads[source][0].tenant_id,
                            source_system=source,
                            sync_at=sync_at,
                            events=events,
                        )
                    )
                else:
                    schedule.append(_holdout_probe_sync(catalog, source, sync_at, slot))

    day11_loads = sorted(
        (load for load in catalog.loads if load.day11_target), key=lambda item: item.logical_id
    )
    for load in day11_loads:
        schedule.append(
            ScheduledSync(
                sync_id=f"{load.logical_id}-D11-06",
                tenant_id=load.tenant_id,
                source_system=load.source_system,
                sync_at=DAY11_SYNC_AT,
                events=(LifecycleEvent(load.logical_id, DAY11_SYNC_AT, status=LoadStatus.ACTIVE),),
            )
        )

    return tuple(
        sorted(schedule, key=lambda sync: (sync.sync_at, sync.source_system, sync.sync_id))
    )


def _scheduled_historical_load(
    source: SourceSystem, loads: tuple[GeneratorLoad, ...], slot: int
) -> tuple[GeneratorLoad, int]:
    """Return a six-stage lifecycle block for one ordinary historical load."""
    declared_order = _ORDINARY_LIFECYCLE_ORDER.get(source)
    by_id = {load.logical_id: load for load in loads}
    ordered = (
        tuple(by_id[load_id] for load_id in declared_order)
        if declared_order is not None
        else tuple(sorted(loads, key=lambda load: load.logical_id))
    )
    if len(ordered) != 6:
        raise ValueError(f"{source.value} requires exactly six historical loads.")
    lifecycle_slots = len(ordered) * len(_STATUSES)
    if slot < lifecycle_slots:
        return ordered[slot // len(_STATUSES)], slot % len(_STATUSES)
    raise ValueError("historical lifecycle slots exhausted.")


def _historical_events_for_slot(
    catalog: ScenarioCatalog,
    source: SourceSystem,
    ordinary_loads: tuple[GeneratorLoad, ...],
    anchors: tuple[GeneratorLoad, ...],
    sync_at: datetime,
    slot: int,
) -> tuple[LifecycleEvent | FinancialEvent, ...]:
    """Schedule batches of anchors without exceeding three changed loads per sync."""
    anchor_events = tuple(
        event
        for start_slot, batch in _anchor_batches(anchors)
        if start_slot <= slot < start_slot + len(_STATUSES)
        for load in batch
        for event in _historical_sync(
            catalog, source, load.logical_id, sync_at, slot - start_slot
        ).events
    )
    ordinary_events = (
        ()
        if slot < len(_STATUSES)
        else tuple(
            event
            for event_index in _ordinary_event_indexes(slot)
            for event in _historical_event(catalog, source, ordinary_loads, sync_at, event_index)
        )
    )
    return anchor_events + ordinary_events


def _anchor_batches(
    anchors: tuple[GeneratorLoad, ...],
) -> tuple[tuple[int, tuple[GeneratorLoad, ...]], ...]:
    """Use three initial anchors, then two per lifecycle window alongside ordinary loads."""
    batches: list[tuple[int, tuple[GeneratorLoad, ...]]] = [(0, anchors[:3])]
    batches.extend(
        (6 * ((index - 1) // 2), anchors[index : index + 2]) for index in range(3, len(anchors), 2)
    )
    return tuple(batches)


def _historical_event(
    catalog: ScenarioCatalog,
    source: SourceSystem,
    loads: tuple[GeneratorLoad, ...],
    sync_at: datetime,
    event_index: int,
) -> tuple[LifecycleEvent | FinancialEvent, ...]:
    load, occurrence = _scheduled_historical_load(source, loads, event_index)
    return _historical_sync(catalog, source, load.logical_id, sync_at, occurrence).events


def _ordinary_event_indexes(slot: int) -> tuple[int, ...]:
    """Fit six ordinary lifecycles after the three-load anchor block."""
    if 6 <= slot < 30:
        return (slot - 6,)
    if 30 <= slot < 36:
        stage = slot - 30
        return 24 + stage, 30 + stage
    raise ValueError("ordinary lifecycle slot is outside the historical window.")


def write_sync_files(data_root: Path, catalog: ScenarioCatalog | None = None) -> tuple[Path, ...]:
    """Write only known generated JSON paths, preserving schema-example JSONC files."""
    catalog = catalog or build_catalog()
    schedule = build_schedule(catalog)
    engine = LifecycleEngine(catalog)
    generated: list[Path] = []
    prior_syncs: list[ScheduledSync] = []
    resolved_root = data_root.resolve()

    for sync in schedule:
        prior_syncs.append(sync)
        payload = serialize_sync(catalog, sync, engine.apply(tuple(prior_syncs)))
        path = data_root / sync_relative_path(sync)
        if not path.resolve().is_relative_to(resolved_root):
            raise ValueError("generated path escapes data root.")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
        generated.append(path)

    return tuple(generated)


def _historical_sync(
    catalog: ScenarioCatalog,
    source: SourceSystem,
    load_id: str,
    sync_at: datetime,
    occurrence: int,
) -> ScheduledSync:
    load = catalog.load(load_id)
    status = _STATUSES[min(occurrence, len(_STATUSES) - 1)]
    booking_rate, final_rate = _rate_outcome(load_id)
    carrier_rate = Money(final_rate if status is LoadStatus.COMPLETED else booking_rate)
    event = LifecycleEvent(
        load_id=load_id,
        occurred_at=sync_at,
        status=status,
        carrier_id=_holdout_carrier(catalog, load_id) if occurrence >= 2 else None,
        customer_rate=Money(carrier_rate.amount + Decimal("280")) if occurrence >= 1 else None,
        carrier_rate=carrier_rate if occurrence >= 2 else None,
        correction_reason=(
            "FINAL_CARRIER_PAY_CORRECTION"
            if status is LoadStatus.COMPLETED and final_rate != booking_rate
            else None
        ),
    )
    events: tuple[LifecycleEvent | FinancialEvent, ...] = (event,)
    if source is SourceSystem.HAULDESK and occurrence == 2:
        events = (
            event,
            FinancialEvent(
                load_id=load_id,
                occurred_at=sync_at,
                entry_id=f"HD-RATE-{load_id}-{sync_at:%Y%m%d%H%M}-{occurrence}",
                side=FinancialSide.PAY,
                code="LINEHAUL",
                amount=Money(booking_rate),
            ),
        )
    elif (
        source is SourceSystem.HAULDESK
        and status is LoadStatus.COMPLETED
        and final_rate != booking_rate
    ):
        events = (
            event,
            FinancialEvent(
                load_id=load_id,
                occurred_at=sync_at,
                entry_id=f"HD-FINAL-{load_id}-{sync_at:%Y%m%d%H%M}-{occurrence}",
                side=FinancialSide.PAY,
                code="ADJUSTMENT",
                amount=Money(final_rate - booking_rate),
            ),
        )
    return ScheduledSync(
        sync_id=f"{source.value}-{sync_at:%Y%m%dT%H%M}",
        tenant_id=load.tenant_id,
        source_system=source,
        sync_at=sync_at,
        events=events,
    )


def _holdout_probe_sync(
    catalog: ScenarioCatalog, source: SourceSystem, sync_at: datetime, slot: int
) -> ScheduledSync:
    """Pack three authored temporal holdouts into each source's final four files."""
    base_slots = len(
        tuple(
            load
            for load in catalog.loads
            if (
                load.source_system is source
                and not load.day11_target
                and not load.evaluation_probe
                and not load.history_anchor
            )
        )
    ) * len(_STATUSES)
    stage = slot - base_slots
    if stage not in range(4):
        raise ValueError("invalid holdout probe stage.")
    probes = tuple(
        sorted(
            (
                load
                for load in catalog.loads
                if load.source_system is source and load.evaluation_probe
            ),
            key=lambda load: load.logical_id,
        )
    )
    if len(probes) != 3:
        raise ValueError(f"{source.value} requires exactly three evaluation probes.")
    statuses = (LoadStatus.PLANNED, LoadStatus.ACTIVE, LoadStatus.COVERED, LoadStatus.COMPLETED)
    status = statuses[stage]
    events: list[LifecycleEvent | FinancialEvent] = []
    for load in probes:
        booking_rate, final_rate = _rate_outcome(load.logical_id)
        carrier_rate = Money(final_rate if status is LoadStatus.COMPLETED else booking_rate)
        event = LifecycleEvent(
            load_id=load.logical_id,
            occurred_at=sync_at,
            status=status,
            carrier_id=_holdout_carrier(catalog, load.logical_id) if stage >= 2 else None,
            customer_rate=Money(carrier_rate.amount + Decimal("280")) if stage >= 2 else None,
            carrier_rate=carrier_rate if stage >= 2 else None,
            correction_reason=(
                "FINAL_CARRIER_PAY_CORRECTION"
                if status is LoadStatus.COMPLETED and final_rate != booking_rate
                else None
            ),
        )
        events.append(event)
        if source is SourceSystem.HAULDESK and stage == 2:
            events.append(
                FinancialEvent(
                    load_id=load.logical_id,
                    occurred_at=sync_at,
                    entry_id=f"HD-HOLDOUT-{load.logical_id}-{sync_at:%Y%m%d%H%M}",
                    side=FinancialSide.PAY,
                    code="LINEHAUL",
                    amount=Money(booking_rate),
                )
            )
        elif (
            source is SourceSystem.HAULDESK
            and status is LoadStatus.COMPLETED
            and final_rate != booking_rate
        ):
            events.append(
                FinancialEvent(
                    load_id=load.logical_id,
                    occurred_at=sync_at,
                    entry_id=f"HD-HOLDOUT-FINAL-{load.logical_id}-{sync_at:%Y%m%d%H%M}",
                    side=FinancialSide.PAY,
                    code="ADJUSTMENT",
                    amount=Money(final_rate - booking_rate),
                )
            )
    return ScheduledSync(
        sync_id=f"{source.value}-{sync_at:%Y%m%dT%H%M}",
        tenant_id=probes[0].tenant_id,
        source_system=source,
        sync_at=sync_at,
        events=tuple(events),
    )


def _holdout_carrier(catalog: ScenarioCatalog, load_id: str) -> str:
    if load_id in _ANCHOR_CARRIERS:
        return _ANCHOR_CARRIERS[load_id]
    try:
        return next(
            holdout.booked_carrier_id
            for holdout in catalog.ranking_holdouts
            if holdout.load_id == load_id
        )
    except StopIteration as error:
        raise ValueError(f"missing ranking holdout label for {load_id}") from error


def _rate_outcome(load_id: str) -> tuple[Decimal, Decimal]:
    """Return authored booking/final carrier-pay facts for one historical outcome."""
    try:
        return _RATE_OUTCOMES[load_id]
    except KeyError as error:
        raise ValueError(f"missing authored rate outcome for {load_id}") from error


def _filename(sync_at: datetime) -> str:
    return f"{sync_at:%Y-%m-%dT%H-%M}_sync.json"


def sync_relative_path(sync: ScheduledSync) -> Path:
    """Return the stable data-root-relative path for one scheduled sync."""
    filename = _filename(sync.sync_at)
    if not _FILENAME_PATTERN.fullmatch(filename):
        raise ValueError(f"invalid generated filename: {filename}")
    return Path(_DIRECTORIES[sync.source_system]) / filename
