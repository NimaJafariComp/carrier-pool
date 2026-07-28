"""Derived machine-readable scenario manifest for generated sync data."""

import json
from pathlib import Path

from carrier_pool.generator.catalog import build_catalog
from carrier_pool.generator.models import ScenarioCatalog, ScenarioDefinition, ScheduledSync
from carrier_pool.generator.scheduler import build_schedule, sync_relative_path

REQUIRED_SCENARIO_IDS = tuple(f"SC-{number:02d}" for number in range(1, 27))


def build_scenarios_manifest(catalog: ScenarioCatalog) -> dict[str, object]:
    """Derive stable manifest data from catalog scenarios and scheduled syncs."""
    scenarios = tuple(sorted(catalog.scenarios, key=lambda scenario: scenario.scenario_id))
    scenario_ids = tuple(scenario.scenario_id for scenario in scenarios)
    if scenario_ids != REQUIRED_SCENARIO_IDS:
        raise ValueError("catalog must define every required Section 9 scenario exactly once.")

    schedule = build_schedule(catalog)
    return {
        "scenario_ids": list(scenario_ids),
        "scenarios": [_manifest_scenario(catalog, schedule, scenario) for scenario in scenarios],
        "ranking_holdouts": [
            {
                "load_id": holdout.load_id,
                "booked_carrier_id": holdout.booked_carrier_id,
                "coverage_tags": list(holdout.coverage_tags),
                "source_files": list(
                    dict.fromkeys(
                        str(sync_relative_path(sync))
                        for sync in schedule
                        if any(event.load_id == holdout.load_id for event in sync.events)
                    )
                ),
            }
            for holdout in catalog.ranking_holdouts
        ],
    }


def write_scenarios_manifest(data_root: Path, catalog: ScenarioCatalog | None = None) -> Path:
    """Write deterministic data/scenarios.json after its referenced syncs exist."""
    manifest = build_scenarios_manifest(catalog or build_catalog())
    path = data_root / "scenarios.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _manifest_scenario(
    catalog: ScenarioCatalog,
    schedule: tuple[ScheduledSync, ...],
    scenario: ScenarioDefinition,
) -> dict[str, object]:
    load_ids = tuple(sorted(scenario.load_ids))
    carrier_ids = tuple(sorted(scenario.carrier_ids))
    customer_ids = tuple(
        sorted({catalog.load(load_id).customer_id for load_id in scenario.load_ids})
    )
    tenant_ids = tuple(
        sorted(
            {
                *(catalog.load(load_id).tenant_id for load_id in scenario.load_ids),
                *(catalog.carrier(carrier_id).tenant_id for carrier_id in scenario.carrier_ids),
            }
        )
    )
    source_by_tenant = {tenant.tenant_id: tenant.source_system.value for tenant in catalog.tenants}
    source_systems = tuple(sorted(source_by_tenant[tenant_id] for tenant_id in tenant_ids))
    source_files = tuple(
        str(sync_relative_path(sync))
        for sync in schedule
        if any(event.load_id in scenario.load_ids for event in sync.events)
        or (
            not scenario.load_ids
            and sync.tenant_id
            in {catalog.carrier(carrier_id).tenant_id for carrier_id in carrier_ids}
        )
    )
    return {
        "scenario_id": scenario.scenario_id,
        "tenant_ids": list(tenant_ids),
        "source_systems": list(source_systems),
        "source_files": list(dict.fromkeys(source_files)),
        "entity_ids": {
            "load_ids": list(load_ids),
            "customer_ids": list(customer_ids),
            "carrier_ids": list(carrier_ids),
        },
        "description": scenario.description,
        "expected_effect": scenario.expected_effect,
        "verification_test": scenario.verification_test,
        "expected_warnings": list(scenario.expected_warnings),
    }
