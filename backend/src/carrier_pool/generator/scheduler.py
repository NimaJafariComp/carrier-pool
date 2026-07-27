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
_CARRIERS = {
    "FF-1001": "FF-C-201",
    "FF-1101": "FF-C-201",
    "FF-1201": "FF-C-202",
    "FF-1301": "FF-C-203",
    "FF-1401": "FF-C-204",
    "FF-1402": "FF-C-205",
    "HD-2101": "HD-C-404",
    "HD-2001": "HD-C-401",
    "HD-2002": "HD-C-405",
    "HD-2003": "HD-C-206",
    "HD-2004": "HD-C-407",
    "HD-2005": "HD-C-408",
    "BO-3001": "BO-C-601",
    "BO-3002": "BO-C-602",
    "BO-3003": "BO-C-603",
    "BO-3005": "BO-C-604",
    "BO-3101": "BO-C-601",
    "BO-3004": "BO-C-602",
}

ANCHOR_LOAD_IDS = {
    SourceSystem.FREIGHTFLOW: "FF-1001",
    SourceSystem.HAULDESK: "HD-2001",
    SourceSystem.BROKEROS: "BO-3001",
}


def build_schedule(catalog: ScenarioCatalog) -> tuple[ScheduledSync, ...]:
    """Create all 120 historical slots and three explicit Day 11 active-load slots."""
    historical_loads = {
        source: tuple(
            load for load in catalog.loads if load.source_system is source and not load.day11_target
        )
        for source in SourceSystem
    }
    if any(not loads for loads in historical_loads.values()):
        raise ValueError("catalog requires at least one historical load per source.")

    schedule: list[ScheduledSync] = []
    for day_offset in range(HISTORICAL_DAYS):
        sync_date = HISTORICAL_START + timedelta(days=day_offset)
        for hour in SYNC_HOURS:
            sync_at = datetime.combine(sync_date, datetime.min.time(), tzinfo=UTC).replace(
                hour=hour
            )
            slot = day_offset * len(SYNC_HOURS) + SYNC_HOURS.index(hour)
            for source in SourceSystem:
                load, occurrence = _scheduled_historical_load(
                    source, historical_loads[source], slot
                )
                schedule.append(
                    _historical_sync(catalog, source, load.logical_id, sync_at, occurrence)
                )

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
    source: SourceSystem, loads: tuple, slot: int
):
    """Return a six-stage lifecycle block; anchor block always runs first."""
    ordered = tuple(
        sorted(
            loads,
            key=lambda load: (load.logical_id != ANCHOR_LOAD_IDS[source], load.logical_id),
        )
    )
    if len(ordered) != 6:
        raise ValueError(f"{source.value} requires exactly six historical loads.")
    lifecycle_slots = len(ordered) * len(_STATUSES)
    if slot < lifecycle_slots:
        return ordered[slot // len(_STATUSES)], slot % len(_STATUSES)
    tail_slot = slot - lifecycle_slots
    return ordered[tail_slot % len(ordered)], len(_STATUSES) + tail_slot // len(ordered)


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
    rate = Money(Decimal("1100") + Decimal(occurrence * 25))
    event = LifecycleEvent(
        load_id=load_id,
        occurred_at=sync_at,
        status=status,
        carrier_id=_CARRIERS[load_id] if occurrence >= 2 else None,
        customer_rate=Money(rate.amount + Decimal("280")) if occurrence >= 1 else None,
        carrier_rate=rate if occurrence >= 2 else None,
    )
    events: tuple[LifecycleEvent | FinancialEvent, ...] = (event,)
    if source is SourceSystem.HAULDESK and occurrence == 2:
        events = (
            event,
            FinancialEvent(
                load_id=load_id,
                occurred_at=sync_at,
                entry_id=f"HD-RATE-{sync_at:%Y%m%d%H%M}-{occurrence}",
                side=FinancialSide.PAY,
                code="LINEHAUL",
                amount=rate,
            ),
        )
    return ScheduledSync(
        sync_id=f"{source.value}-{sync_at:%Y%m%dT%H%M}",
        tenant_id=load.tenant_id,
        source_system=source,
        sync_at=sync_at,
        events=events,
    )


def _filename(sync_at: datetime) -> str:
    return f"{sync_at:%Y-%m-%dT%H-%M}_sync.json"


def sync_relative_path(sync: ScheduledSync) -> Path:
    """Return the stable data-root-relative path for one scheduled sync."""
    filename = _filename(sync.sync_at)
    if not _FILENAME_PATTERN.fullmatch(filename):
        raise ValueError(f"invalid generated filename: {filename}")
    return Path(_DIRECTORIES[sync.source_system]) / filename
