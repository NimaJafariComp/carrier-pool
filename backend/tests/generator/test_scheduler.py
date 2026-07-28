"""Phase 6.5 deterministic generated-sync schedule and writer tests."""

import json
import re
from datetime import UTC
from pathlib import Path

from carrier_pool.domain.types import LoadStatus
from carrier_pool.generator.catalog import build_catalog
from carrier_pool.generator.models import FinancialEvent, LifecycleEvent
from carrier_pool.generator.scheduler import (
    _RATE_OUTCOMES,
    ANCHOR_LOAD_IDS,
    DAY11_SYNC_AT,
    HISTORICAL_SYNC_COUNT,
    build_schedule,
    write_sync_files,
)


def test_schedule_has_all_historical_slots_and_day11_active_files() -> None:
    schedule = build_schedule(build_catalog())
    historical = tuple(sync for sync in schedule if sync.sync_at < DAY11_SYNC_AT)
    day11 = tuple(sync for sync in schedule if sync.sync_at == DAY11_SYNC_AT)

    assert len(historical) == HISTORICAL_SYNC_COUNT
    assert len(day11) == 3
    for source_syncs in _by_source(historical).values():
        assert len(source_syncs) == 40
        assert [sync.sync_at.hour for sync in source_syncs] == [0, 6, 12, 18] * 10
        assert all(1 <= len({event.load_id for event in sync.events}) <= 3 for sync in source_syncs)
    assert all(sync.events[0].occurred_at.tzinfo is UTC for sync in schedule)


def test_early_anchor_completes_before_later_loads_first_become_active() -> None:
    schedule = build_schedule(build_catalog())
    for source, anchor_ids in ANCHOR_LOAD_IDS.items():
        source_events = tuple(
            (sync.sync_at, event)
            for sync in schedule
            if sync.source_system is source
            for event in sync.events
            if isinstance(event, LifecycleEvent)
        )
        anchor_completed_at = {
            anchor_id: next(
                sync_at
                for sync_at, event in source_events
                if event.load_id == anchor_id
                and event.status is not None
                and event.status.value == "COMPLETED"
            )
            for anchor_id in anchor_ids
        }
        later_first_active = {
            event.load_id: sync_at
            for sync_at, event in source_events
            if event.load_id not in anchor_ids
            and event.status is not None
            and event.status.value == "ACTIVE"
        }
        assert later_first_active
        assert all(
            completed_at < active_at
            for completed_at in anchor_completed_at.values()
            for active_at in later_first_active.values()
        )


def test_anchor_catalog_represents_diverse_completed_carrier_history() -> None:
    """Demo candidates must have authored work, not merely carrier master records."""
    catalog = build_catalog()
    completed_carriers: dict[str, set[str]] = {}
    for sync in build_schedule(catalog):
        for event in sync.events:
            if (
                isinstance(event, LifecycleEvent)
                and event.status is LoadStatus.COMPLETED
                and event.carrier_id is not None
            ):
                completed_carriers.setdefault(sync.tenant_id, set()).add(event.carrier_id)

    assert len(completed_carriers["ff-broker"]) >= 5
    assert len(completed_carriers["hd-broker"]) >= 7
    assert len(completed_carriers["bo-broker"]) == 8


def test_hauldesk_load_emits_one_booking_linehaul_not_status_adjustments() -> None:
    schedule = build_schedule(build_catalog())
    entries = tuple(
        event
        for sync in schedule
        for event in sync.events
        if isinstance(event, FinancialEvent) and event.load_id == "HD-2101"
    )

    assert [(entry.code, entry.amount.amount) for entry in entries] == [("LINEHAUL", 1150)]


def test_delivery_proximity_examples_have_distinct_historical_recency() -> None:
    completions = {
        event.load_id: sync.sync_at
        for sync in build_schedule(build_catalog())
        for event in sync.events
        if isinstance(event, LifecycleEvent)
        and event.load_id in {"FF-1401", "FF-1402"}
        and event.status is LoadStatus.COMPLETED
    }

    assert completions["FF-1402"] < completions["FF-1401"]
    assert (completions["FF-1401"] - completions["FF-1402"]).days >= 5


def test_rate_outcomes_are_authored_and_final_corrections_are_source_accurate() -> None:
    catalog = build_catalog()
    historical_ids = {load.logical_id for load in catalog.loads if not load.day11_target}
    assert set(_RATE_OUTCOMES) == historical_ids
    assert _RATE_OUTCOMES["FF-1001"] != _RATE_OUTCOMES["FF-1101"]
    assert _RATE_OUTCOMES["FF-1301"][1] > _RATE_OUTCOMES["FF-1401"][1]

    lifecycle_events = {
        event.load_id: event
        for sync in build_schedule(catalog)
        for event in sync.events
        if isinstance(event, LifecycleEvent) and event.status is LoadStatus.COMPLETED
    }
    assert lifecycle_events["FF-1101"].carrier_rate is not None
    assert lifecycle_events["FF-1101"].carrier_rate.amount == 1265
    assert lifecycle_events["FF-1101"].correction_reason == "FINAL_CARRIER_PAY_CORRECTION"

    hauldesk_entries = {
        load_id: [
            event
            for sync in build_schedule(catalog)
            for event in sync.events
            if isinstance(event, FinancialEvent) and event.load_id == load_id
        ]
        for load_id in ("HD-2002", "HD-2005")
    }
    assert [(item.code, item.amount.amount) for item in hauldesk_entries["HD-2002"]] == [
        ("LINEHAUL", 1190),
        ("ADJUSTMENT", 35),
    ]
    assert [(item.code, item.amount.amount) for item in hauldesk_entries["HD-2005"]] == [
        ("LINEHAUL", 1600),
        ("ADJUSTMENT", -25),
    ]


def test_authored_holdouts_are_labeled_only_after_first_active() -> None:
    catalog = build_catalog()
    labels = {item.load_id: item.booked_carrier_id for item in catalog.ranking_holdouts}
    events = {
        load_id: [
            event
            for sync in build_schedule(catalog)
            for event in sync.events
            if isinstance(event, LifecycleEvent) and event.load_id == load_id
        ]
        for load_id in labels
    }

    for load_id, booked_carrier_id in labels.items():
        lifecycle = events[load_id]
        assert lifecycle[0].status is LoadStatus.PLANNED
        assert lifecycle[1].status is LoadStatus.ACTIVE
        assert lifecycle[0].carrier_id is None
        assert lifecycle[1].carrier_id is None
        assert all(event.carrier_id == booked_carrier_id for event in lifecycle[2:])


def test_rich_ranking_holdouts_have_three_completed_carrier_loads_at_activation() -> None:
    """A RICH tag must describe evidence available at the historical cutoff."""
    catalog = build_catalog()
    holdouts = {item.load_id: item for item in catalog.ranking_holdouts}
    completed: dict[tuple[str, str], int] = {}
    completed_carriers: dict[str, str] = {}

    for sync in build_schedule(catalog):
        for event in sync.events:
            if not isinstance(event, LifecycleEvent):
                continue
            holdout = holdouts.get(event.load_id)
            if event.status is LoadStatus.ACTIVE and holdout and "RICH" in holdout.coverage_tags:
                assert completed.get((sync.tenant_id, holdout.booked_carrier_id), 0) >= 3
                target = catalog.load(event.load_id)
                matching_anchors = tuple(
                    load
                    for load in catalog.loads
                    if load.history_anchor
                    and load.tenant_id == target.tenant_id
                    and load.equipment is target.equipment
                    and load.stops == target.stops
                )
                assert len(matching_anchors) >= 3
                assert all(
                    completed_carriers[load.logical_id] == holdout.booked_carrier_id
                    for load in matching_anchors
                )
            if event.status is LoadStatus.COMPLETED and event.carrier_id:
                key = (sync.tenant_id, event.carrier_id)
                completed[key] = completed.get(key, 0) + 1
                completed_carriers[event.load_id] = event.carrier_id


def test_writer_uses_strict_names_and_is_byte_deterministic(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    example = data_root / "tms_a_freightflow" / "example_sync.jsonc"
    example.parent.mkdir(parents=True)
    example.write_text('// schema documentation\\n{\\n  \\"loads\\": []\\n}\\n')

    first_paths = write_sync_files(data_root)
    first_bytes = {path.relative_to(data_root): path.read_bytes() for path in first_paths}
    second_paths = write_sync_files(data_root)
    second_bytes = {path.relative_to(data_root): path.read_bytes() for path in second_paths}

    assert len(first_paths) == 123
    assert tuple(path.relative_to(data_root) for path in first_paths) == tuple(
        path.relative_to(data_root) for path in second_paths
    )
    assert second_bytes == first_bytes
    assert example.read_text() == '// schema documentation\\n{\\n  \\"loads\\": []\\n}\\n'
    assert all(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}_sync\.json", path.name) for path in first_paths
    )
    assert all("//" not in content.decode() for content in first_bytes.values())
    assert all(json.loads(content) for content in first_bytes.values())
    assert {path.name for path in first_paths if path.name.startswith("2026-07-11")} == {
        "2026-07-11T06-00_sync.json"
    }


def _by_source(schedule):
    return {
        source: tuple(sync for sync in schedule if sync.source_system is source)
        for source in {sync.source_system for sync in schedule}
    }
