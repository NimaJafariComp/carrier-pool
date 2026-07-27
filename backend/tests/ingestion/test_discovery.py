"""Phase 7.1 sync-file discovery and chronological orchestration tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from carrier_pool.domain.types import SourceSystem
from carrier_pool.ingestion.discovery import (
    FileIngestionOrchestrator,
    InvalidGeneratedSyncFilename,
    SourceBinding,
    discover_sync_files,
)


def _binding(directory: Path, tenant_id: str, source: SourceSystem) -> SourceBinding:
    directory.mkdir(parents=True, exist_ok=True)
    return SourceBinding(directory, tenant_id, source)


def test_discovery_binds_directories_ignores_jsonc_and_orders_globally(tmp_path: Path) -> None:
    freightflow = _binding(tmp_path / "freightflow", "tenant-ff", SourceSystem.FREIGHTFLOW)
    hauldesk = _binding(tmp_path / "hauldesk", "tenant-hd", SourceSystem.HAULDESK)
    (freightflow.directory / "example_sync.jsonc").write_text("// docs")
    (freightflow.directory / "notes.txt").write_text("ignore")
    (freightflow.directory / "2026-07-01T12-00_sync.json").write_text("{}")
    (hauldesk.directory / "2026-07-01T06-00_sync.json").write_text("{}")
    (freightflow.directory / "2026-07-01T06-00_sync.json").write_text("{}")

    discovered = discover_sync_files((freightflow, hauldesk))

    assert [(item.binding.tenant_id, item.path.name) for item in discovered] == [
        ("tenant-ff", "2026-07-01T06-00_sync.json"),
        ("tenant-hd", "2026-07-01T06-00_sync.json"),
        ("tenant-ff", "2026-07-01T12-00_sync.json"),
    ]
    assert discovered[0].sync_at == datetime(2026, 7, 1, 6, tzinfo=UTC)


def test_discovery_rejects_invalid_json_filename(tmp_path: Path) -> None:
    binding = _binding(tmp_path / "freightflow", "tenant-ff", SourceSystem.FREIGHTFLOW)
    (binding.directory / "bad_sync.json").write_text("{}")

    with pytest.raises(InvalidGeneratedSyncFilename, match="bad_sync.json"):
        discover_sync_files((binding,))


def test_orchestrator_passes_one_file_at_a_time_with_bound_context(tmp_path: Path) -> None:
    binding = _binding(tmp_path / "brokeros", "tenant-bo", SourceSystem.BROKEROS)
    (binding.directory / "2026-07-01T00-00_sync.json").write_text("first")
    (binding.directory / "2026-07-01T06-00_sync.json").write_text("second")
    received: list[tuple[str, str, SourceSystem, bytes]] = []

    orchestrator = FileIngestionOrchestrator((binding,))
    results = orchestrator.ingest_all(
        lambda sync: received.append(
            (
                sync.path.name,
                sync.binding.tenant_id,
                sync.binding.source_system,
                sync.path.read_bytes(),
            )
        )
    )

    assert results == (None, None)
    assert received == [
        ("2026-07-01T00-00_sync.json", "tenant-bo", SourceSystem.BROKEROS, b"first"),
        ("2026-07-01T06-00_sync.json", "tenant-bo", SourceSystem.BROKEROS, b"second"),
    ]


def test_binding_rejects_directory_reused_by_another_tenant(tmp_path: Path) -> None:
    directory = tmp_path / "shared"
    first = _binding(directory, "tenant-ff", SourceSystem.FREIGHTFLOW)
    second = SourceBinding(directory, "tenant-hd", SourceSystem.HAULDESK)

    with pytest.raises(ValueError, match="directory bindings must be unique"):
        discover_sync_files((first, second))
