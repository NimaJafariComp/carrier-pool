"""Phase 6.6 derived scenario-manifest tests."""

import json
from pathlib import Path

from carrier_pool.generator.catalog import build_catalog
from carrier_pool.generator.manifest import REQUIRED_SCENARIO_IDS, write_scenarios_manifest
from carrier_pool.generator.scheduler import write_sync_files


def test_manifest_covers_required_scenarios_with_valid_files_and_entities(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    catalog = build_catalog()
    write_sync_files(data_root, catalog)
    manifest_path = write_scenarios_manifest(data_root, catalog)
    manifest = json.loads(manifest_path.read_text())

    assert manifest["scenario_ids"] == list(REQUIRED_SCENARIO_IDS)
    assert len(manifest["scenarios"]) == len(REQUIRED_SCENARIO_IDS)
    load_ids = {load.logical_id for load in catalog.loads}
    customer_ids = {customer.customer_id for customer in catalog.customers}
    carrier_ids = {carrier.carrier_id for carrier in catalog.carriers}
    for scenario in manifest["scenarios"]:
        assert scenario["source_files"]
        assert all((data_root / source_file).is_file() for source_file in scenario["source_files"])
        assert set(scenario["entity_ids"]["load_ids"]) <= load_ids
        assert set(scenario["entity_ids"]["customer_ids"]) <= customer_ids
        assert set(scenario["entity_ids"]["carrier_ids"]) <= carrier_ids
        assert scenario["description"]
        assert scenario["expected_effect"]
        assert scenario["verification_test"].startswith("test_sc")
        assert isinstance(scenario["expected_warnings"], list)


def test_manifest_is_derived_deterministically_not_hand_written(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    catalog = build_catalog()
    write_sync_files(data_root, catalog)

    first = write_scenarios_manifest(data_root, catalog).read_bytes()
    second = write_scenarios_manifest(data_root, catalog).read_bytes()

    assert first == second
