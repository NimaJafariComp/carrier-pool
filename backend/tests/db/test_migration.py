"""Migration structure tests for Phase 3.2."""

from pathlib import Path


def test_tenant_ingestion_migration_declares_upgrade_and_downgrade() -> None:
    migration = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260727_01_tenant_ingestion_metadata.py"
    )
    contents = migration.read_text()

    assert 'revision: str = "20260727_01"' in contents
    assert 'op.create_table(\n        "tenants"' in contents
    assert 'op.create_table(\n        "ingestion_files"' in contents
    assert 'op.drop_table("ingestion_files")' in contents
    assert 'op.drop_table("tenants")' in contents


def test_canonical_entities_migration_declares_immutable_version_tables() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "versions" / "20260727_02_canonical_entities.py"
    )
    contents = migration.read_text()

    assert 'revision: str = "20260727_02"' in contents
    assert 'op.create_table(\n        "load_versions"' in contents
    assert 'op.create_table(\n        "stops"' in contents
    assert "CREATE TRIGGER load_versions_immutable" in contents
    assert 'op.drop_table("load_versions")' in contents


def test_rates_and_decisions_migration_declares_immutable_outputs() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "versions" / "20260727_03_rates_and_decisions.py"
    )
    contents = migration.read_text()

    assert 'revision: str = "20260727_03"' in contents
    assert '"source_rate_entries"' in contents
    assert '"decision_runs"' in contents
    assert '"carrier_recommendations"' in contents
    assert "prevent_decision_mutation" in contents
