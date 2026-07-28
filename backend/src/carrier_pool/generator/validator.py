"""Database-free semantic validation for deterministic generated sync data."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from re import compile as re_compile
from typing import cast

from carrier_pool.domain.models import NormalizedSync
from carrier_pool.domain.types import LoadStatus, SourceSystem
from carrier_pool.generator.catalog import build_catalog
from carrier_pool.generator.manifest import REQUIRED_SCENARIO_IDS
from carrier_pool.generator.models import LifecycleEvent, ScenarioCatalog, ScheduledSync
from carrier_pool.generator.scheduler import (
    ANCHOR_LOAD_IDS,
    DAY11_SYNC_AT,
    build_schedule,
    sync_relative_path,
)
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.brokeros import BrokerOSAdapter
from carrier_pool.ingestion.freightflow import FreightFlowAdapter
from carrier_pool.ingestion.hauldesk import HaulDeskAdapter

_SYNC_NAME = re_compile(r"\d{4}-\d{2}-\d{2}T(?:00|06|12|18)-00_sync\.json\Z")
_DIRECTORIES = (
    "tms_a_freightflow",
    "tms_b_hauldesk",
    "tms_c_brokeros",
)
_ADAPTERS = {
    SourceSystem.FREIGHTFLOW: FreightFlowAdapter(),
    SourceSystem.HAULDESK: HaulDeskAdapter(),
    SourceSystem.BROKEROS: BrokerOSAdapter(),
}
MINIMUM_COMPLETED_HISTORY_LOADS_PER_SOURCE = 6


class GeneratedDataValidationError(ValueError):
    """Raised when generated files violate a structural or semantic contract."""


@dataclass(frozen=True, slots=True)
class ValidationReport:
    sync_file_count: int
    warning_codes: tuple[str, ...]


def validate_generated_data(
    data_root: Path, catalog: ScenarioCatalog | None = None
) -> ValidationReport:
    """Validate generated files, manifest links, and canonical observations without DB writes."""
    catalog = catalog or build_catalog()
    errors: list[str] = []
    expected_syncs = build_schedule(catalog)
    try:
        validate_schedule_backtest_readiness(catalog, expected_syncs)
    except GeneratedDataValidationError as error:
        errors.append(str(error))
    expected_by_path = {str(sync_relative_path(sync)): sync for sync in expected_syncs}
    actual_paths = _actual_sync_paths(data_root, errors)
    expected_paths = set(expected_by_path)
    actual_path_set = set(actual_paths)
    missing = sorted(expected_paths - actual_path_set)
    extra = sorted(actual_path_set - expected_paths)
    if missing:
        errors.append(f"missing expected sync files: {', '.join(missing)}")
    if extra:
        errors.append(f"unexpected sync files: {', '.join(extra)}")

    manifest = _read_manifest(data_root, errors)
    expected_warnings = _expected_warning_codes(manifest, errors)
    _validate_manifest_scenarios(manifest, expected_paths, errors)

    seen_rate_ids: set[str] = set()
    seen_loads: dict[tuple[str, str, str], tuple[str, str]] = {}
    covered_loads: set[tuple[str, str, str]] = set()
    warning_codes: set[str] = set()
    for relative_path in sorted(actual_path_set & expected_paths):
        sync = expected_by_path[relative_path]
        normalized = _parse_and_normalize(data_root / relative_path, sync, errors)
        if normalized is None:
            continue
        _validate_normalized_sync(
            normalized,
            sync,
            catalog,
            seen_rate_ids,
            seen_loads,
            covered_loads,
            expected_warnings.get(relative_path, set()),
            warning_codes,
            errors,
        )

    if errors:
        raise GeneratedDataValidationError("\n".join(errors))
    return ValidationReport(len(actual_path_set), tuple(sorted(warning_codes)))


def validate_schedule_backtest_readiness(
    catalog: ScenarioCatalog, schedule: tuple[ScheduledSync, ...]
) -> None:
    """Require completed source-local history before rolling backtest targets."""
    errors: list[str] = []
    for source in SourceSystem:
        historical_ids = {
            load.logical_id
            for load in catalog.loads
            if (
                load.source_system is source
                and not load.day11_target
                and not load.evaluation_probe
                and not load.history_anchor
            )
        }
        anchor_ids = ANCHOR_LOAD_IDS[source]
        lifecycle_events = tuple(
            (sync.sync_at, event)
            for sync in schedule
            if sync.source_system is source
            for event in sync.events
            if isinstance(event, LifecycleEvent) and event.load_id in (*historical_ids, *anchor_ids)
        )
        completed_at: dict[str, datetime] = {}
        for sync_at, event in lifecycle_events:
            if event.status is LoadStatus.COMPLETED:
                completed_at.setdefault(event.load_id, sync_at)
        if len(set(completed_at) & historical_ids) < MINIMUM_COMPLETED_HISTORY_LOADS_PER_SOURCE:
            errors.append(
                f"{source.value} requires six completed historical loads for rolling backtests"
            )
            continue
        anchor_completed_at = {anchor_id: completed_at.get(anchor_id) for anchor_id in anchor_ids}
        if any(value is None for value in anchor_completed_at.values()):
            errors.append(f"{source.value} early history anchors must complete")
            continue
        first_active_at: dict[str, datetime] = {}
        for sync_at, event in lifecycle_events:
            if event.status is LoadStatus.ACTIVE and event.load_id not in anchor_ids:
                first_active_at.setdefault(event.load_id, sync_at)
        if any(
            completed_at is not None and completed_at >= active_at
            for completed_at in anchor_completed_at.values()
            for active_at in first_active_at.values()
        ):
            errors.append(
                f"{source.value} early history anchors must complete before later loads "
                "become ACTIVE"
            )
    if errors:
        raise GeneratedDataValidationError("\n".join(errors))


def _actual_sync_paths(data_root: Path, errors: list[str]) -> set[str]:
    paths: set[str] = set()
    for directory in _DIRECTORIES:
        source_directory = data_root / directory
        if not source_directory.is_dir():
            errors.append(f"missing source directory: {directory}")
            continue
        for path in source_directory.glob("*.json"):
            relative_path = str(path.relative_to(data_root))
            if not _SYNC_NAME.fullmatch(path.name):
                errors.append(f"invalid sync filename: {relative_path}")
            else:
                paths.add(relative_path)
    return paths


def _read_manifest(data_root: Path, errors: list[str]) -> dict[str, object]:
    path = data_root / "scenarios.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"invalid scenarios manifest: {error}")
        return {}
    if not isinstance(payload, dict):
        errors.append("invalid scenarios manifest: root must be an object")
        return {}
    return cast(dict[str, object], payload)


def _expected_warning_codes(manifest: dict[str, object], errors: list[str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list):
        errors.append("invalid scenarios manifest: scenarios must be a list")
        return result
    for item in cast(list[object], scenarios):
        if not isinstance(item, dict):
            errors.append("invalid scenarios manifest: scenario must be an object")
            continue
        scenario = cast(dict[str, object], item)
        source_files = scenario.get("source_files")
        warnings = scenario.get("expected_warnings")
        if not isinstance(source_files, list) or not isinstance(warnings, list):
            errors.append("invalid scenarios manifest: source_files/warnings must be lists")
            continue
        values = cast(list[object], source_files) + cast(list[object], warnings)
        if not all(isinstance(value, str) for value in values):
            errors.append("invalid scenarios manifest: source_files/warnings must contain strings")
            continue
        typed_source_files = cast(list[str], source_files)
        typed_warnings = cast(list[str], warnings)
        for source_file in typed_source_files:
            result.setdefault(source_file, set()).update(typed_warnings)
    return result


def _validate_manifest_scenarios(
    manifest: dict[str, object], expected_paths: set[str], errors: list[str]
) -> None:
    scenario_ids = manifest.get("scenario_ids")
    scenarios = manifest.get("scenarios")
    if scenario_ids != list(REQUIRED_SCENARIO_IDS) or not isinstance(scenarios, list):
        errors.append("manifest does not declare every required scenario")
        return
    records: dict[str, dict[str, object]] = {}
    for item in cast(list[object], scenarios):
        if not isinstance(item, dict):
            continue
        record = cast(dict[str, object], item)
        scenario_id = record.get("scenario_id")
        if isinstance(scenario_id, str):
            records[scenario_id] = record
    for scenario_id in REQUIRED_SCENARIO_IDS:
        record = records.get(scenario_id)
        if record is None:
            errors.append(f"manifest missing required scenario: {scenario_id}")
            continue
        source_files = record.get("source_files")
        if not isinstance(source_files, list) or not source_files:
            errors.append(f"scenario {scenario_id} has no source files")
        elif not all(
            isinstance(source_file, str) for source_file in cast(list[object], source_files)
        ):
            errors.append(f"scenario {scenario_id} has non-string source file")
        elif any(
            source_file not in expected_paths for source_file in cast(list[str], source_files)
        ):
            errors.append(f"scenario {scenario_id} references invalid source file")
    for scenario_id in (
        "SC-01",
        "SC-02",
        "SC-03",
        "SC-12",
        "SC-14",
        "SC-15",
        "SC-16",
        "SC-24",
        "SC-25",
        "SC-26",
    ):
        if scenario_id not in records:
            errors.append(f"required history scenario missing: {scenario_id}")


def _parse_and_normalize(
    path: Path, sync: ScheduledSync, errors: list[str]
) -> NormalizedSync | None:
    try:
        content = path.read_bytes()
        if b"//" in content:
            raise ValueError("plain JSON sync files must not contain comments")
        adapter = _ADAPTERS[sync.source_system]
        tenant = TenantContext(sync.tenant_id)
        parsed = adapter.parse_file(SourceFile(path, content), tenant)
        return adapter.normalize(parsed, tenant)
    except Exception as error:
        errors.append(f"{path.relative_to(path.parents[1])}: {error}")
        return None


def _validate_normalized_sync(
    normalized: NormalizedSync,
    scheduled: ScheduledSync,
    catalog: ScenarioCatalog,
    seen_rate_ids: set[str],
    seen_loads: dict[tuple[str, str, str], tuple[str, str]],
    covered_loads: set[tuple[str, str, str]],
    allowed_warning_codes: set[str],
    warning_codes: set[str],
    errors: list[str],
) -> None:
    label = scheduled.sync_id
    if normalized.metadata.sync_at != scheduled.sync_at:
        errors.append(f"{label}: payload timestamp does not match filename timestamp")
    if not 1 <= len(normalized.loads) <= 3:
        errors.append(f"{label}: expected one to three changed loads")
    known_zips = {location.postal_code for location in catalog.locations}
    for warning in normalized.warnings:
        warning_codes.add(warning.code)
        if warning.code not in allowed_warning_codes:
            errors.append(f"{label}: unexpected normalization warning {warning.code}")
    for load in normalized.loads:
        identity = (
            load.identity.tenant_id,
            load.identity.source_system.value,
            str(load.identity.external_id),
        )
        customer = (str(load.customer.identity.external_id), load.customer.name)
        prior_customer = seen_loads.setdefault(identity, customer)
        if prior_customer != customer:
            errors.append(f"{label}: unstable customer identity for load {identity[2]}")
        if (
            load.source_created_at > load.source_modified_at
            or load.source_modified_at > scheduled.sync_at
        ):
            errors.append(f"{label}: inconsistent load timestamps")
        if load.weight_lbs is None or load.weight_lbs <= 0:
            errors.append(f"{label}: load weight must be positive")
        if load.distance_miles is None or load.distance_miles <= 0:
            errors.append(f"{label}: load distance must be positive")
        if any(stop.postal_code not in known_zips for stop in load.stops):
            errors.append(f"{label}: stop ZIP missing from location catalog")
        if load.status is LoadStatus.COVERED:
            covered_loads.add(identity)
        if load.carrier_rate is not None and identity not in covered_loads:
            errors.append(f"{label}: carrier rate precedes covered lifecycle state")
    for entry in normalized.source_financial_entries:
        entry_id = str(entry.identity.external_id)
        if entry_id in seen_rate_ids:
            errors.append(f"{label}: duplicate append-only rate ID {entry_id}")
        seen_rate_ids.add(entry_id)
        if entry.source_created_at > scheduled.sync_at:
            errors.append(f"{label}: financial entry occurs after sync timestamp")
    if scheduled.sync_at == DAY11_SYNC_AT:
        for load in normalized.loads:
            if (
                load.status is not LoadStatus.ACTIVE
                or load.carrier is not None
                or load.carrier_rate
            ):
                errors.append(f"{label}: Day 11 target must remain active and unassigned")
