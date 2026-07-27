"""Database-free contracts for source parsing and canonical normalization."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from carrier_pool.domain.models import NormalizedSync
from carrier_pool.domain.types import SourceSystem


@dataclass(frozen=True, slots=True)
class TenantContext:
    """The tenant scope supplied explicitly to every source adapter operation."""

    tenant_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id must not be empty.")


@dataclass(frozen=True, slots=True)
class SourceFile:
    """A source sync file's location and unparsed bytes."""

    path: Path
    content: bytes


@dataclass(frozen=True, slots=True)
class ParsedSync:
    """Validated source DTO payload awaiting canonical normalization."""

    source_system: SourceSystem
    source_file: SourceFile
    payload: object


class SourceAdapterError(ValueError):
    """Base error for source adapter failures that occur before persistence."""


class InvalidSourceFileError(SourceAdapterError):
    """Raised when a source file cannot be parsed or fails source validation."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"Invalid source file {path}: {message}")


class UnsupportedSourceValueError(SourceAdapterError):
    """Raised when an adapter receives an undocumented source value."""

    def __init__(self, source_system: SourceSystem, field_path: str, value: object) -> None:
        super().__init__(f"Unsupported {source_system.value} value at {field_path}: {value!r}.")


class SourceAdapter(Protocol):
    """Parse one TMS file, then normalize it without performing persistence."""

    source_system: SourceSystem

    def parse_file(self, source_file: SourceFile, tenant: TenantContext) -> ParsedSync:
        """Parse source-specific DTOs from supplied file bytes."""
        ...

    def normalize(self, parsed_sync: ParsedSync, tenant: TenantContext) -> NormalizedSync:
        """Convert parsed DTOs into immutable canonical snapshots and facts."""
        ...
