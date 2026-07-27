"""Phase 6.5 deterministic generated-sync schedule and writer tests."""

import json
import re
from datetime import UTC
from pathlib import Path

from carrier_pool.generator.catalog import build_catalog
from carrier_pool.generator.scheduler import (
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
