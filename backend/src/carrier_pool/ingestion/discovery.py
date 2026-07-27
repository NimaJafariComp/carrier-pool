"""Strict generated-sync discovery and single-file ingestion orchestration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from re import compile as re_compile

from carrier_pool.domain.types import SourceSystem

_SYNC_FILENAME = re_compile(r"\d{4}-\d{2}-\d{2}T(?:00|06|12|18)-00_sync\.json\Z")


class InvalidGeneratedSyncFilename(ValueError):
    """Raised for JSON files that do not use the generated sync filename contract."""


@dataclass(frozen=True, slots=True)
class SourceBinding:
    """One trusted directory-to-tenant/source mapping."""

    directory: Path
    tenant_id: str
    source_system: SourceSystem

    def __post_init__(self) -> None:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id must not be empty.")


@dataclass(frozen=True, slots=True)
class DiscoveredSync:
    """One validated generated file with its trusted binding and UTC filename timestamp."""

    path: Path
    binding: SourceBinding
    sync_at: datetime


def discover_sync_files(bindings: tuple[SourceBinding, ...]) -> tuple[DiscoveredSync, ...]:
    """Discover strict JSON sync files and return globally chronological work items."""
    _validate_bindings(bindings)
    discovered: list[DiscoveredSync] = []
    for binding in bindings:
        if not binding.directory.is_dir():
            raise ValueError(f"source directory does not exist: {binding.directory}")
        for path in binding.directory.glob("*.json"):
            discovered.append(discover_sync_file(path, binding))
    return tuple(
        sorted(
            discovered,
            key=lambda item: (item.sync_at, item.binding.source_system.value, item.path.name),
        )
    )


def discover_sync_file(path: Path, binding: SourceBinding) -> DiscoveredSync:
    """Validate one file belongs to a binding and parse its filename timestamp."""
    if path.parent.resolve() != binding.directory.resolve():
        raise ValueError("sync file must be inside its explicitly bound source directory.")
    if not _SYNC_FILENAME.fullmatch(path.name):
        raise InvalidGeneratedSyncFilename(f"invalid generated sync filename: {path.name}")
    timestamp = datetime.strptime(path.name.removesuffix("_sync.json"), "%Y-%m-%dT%H-%M")
    return DiscoveredSync(path=path, binding=binding, sync_at=timestamp.replace(tzinfo=UTC))


class FileIngestionOrchestrator:
    """Discover and submit one chronologically ordered sync at a time."""

    def __init__(self, bindings: tuple[SourceBinding, ...]) -> None:
        _validate_bindings(bindings)
        self._bindings = bindings

    def discover(self) -> tuple[DiscoveredSync, ...]:
        return discover_sync_files(self._bindings)

    def ingest_all[T](self, ingest_one: Callable[[DiscoveredSync], T]) -> tuple[T, ...]:
        return tuple(ingest_one(sync) for sync in self.discover())


def _validate_bindings(bindings: tuple[SourceBinding, ...]) -> None:
    directories = tuple(binding.directory.resolve() for binding in bindings)
    if len(directories) != len(set(directories)):
        raise ValueError("directory bindings must be unique.")
    sources = tuple(binding.source_system for binding in bindings)
    if len(sources) != len(set(sources)):
        raise ValueError("source-system bindings must be unique.")
