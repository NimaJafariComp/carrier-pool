"""Phase 6.7 generated-data validation tests."""

import json
from pathlib import Path

import pytest

from carrier_pool.generator.manifest import write_scenarios_manifest
from carrier_pool.generator.scheduler import write_sync_files
from carrier_pool.generator.validator import GeneratedDataValidationError, validate_generated_data


def _generated_data(tmp_path: Path) -> Path:
    data_root = tmp_path / "data"
    write_sync_files(data_root)
    write_scenarios_manifest(data_root)
    return data_root


def test_validator_accepts_complete_generated_dataset(tmp_path: Path) -> None:
    report = validate_generated_data(_generated_data(tmp_path))

    assert report.sync_file_count == 123
    assert report.warning_codes == ()


def test_validator_rejects_malformed_schedule(tmp_path: Path) -> None:
    data_root = _generated_data(tmp_path)
    (data_root / "tms_a_freightflow" / "2026-07-02T06-00_sync.json").unlink()

    with pytest.raises(GeneratedDataValidationError, match="missing expected sync files"):
        validate_generated_data(data_root)


def test_validator_rejects_broken_brokeros_reference(tmp_path: Path) -> None:
    data_root = _generated_data(tmp_path)
    path = data_root / "tms_c_brokeros" / "2026-07-01T00-00_sync.json"
    payload = json.loads(path.read_text())
    payload["referenced_records"] = {}
    path.write_text(json.dumps(payload))

    with pytest.raises(GeneratedDataValidationError, match="Missing Account reference"):
        validate_generated_data(data_root)


def test_validator_rejects_undeclared_normalization_warning(tmp_path: Path) -> None:
    data_root = _generated_data(tmp_path)
    path = data_root / "tms_b_hauldesk" / "2026-07-01T00-00_sync.json"
    payload = json.loads(path.read_text())
    payload["loads"][0]["carrier_ref"] = 999999
    path.write_text(json.dumps(payload))

    with pytest.raises(GeneratedDataValidationError, match="unexpected normalization warning"):
        validate_generated_data(data_root)
