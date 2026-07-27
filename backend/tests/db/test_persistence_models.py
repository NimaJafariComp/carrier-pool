"""Tests for Phase 3.2 tenant and ingestion persistence metadata."""

from carrier_pool.db.base import Base


def test_phase_3_metadata_contains_only_implemented_tables() -> None:
    assert set(Base.metadata.tables) == {
        "tenants",
        "ingestion_files",
        "customers",
        "carriers",
        "carrier_versions",
        "loads",
        "load_versions",
        "stops",
        "source_rate_entries",
        "decision_runs",
        "carrier_recommendations",
    }


def test_ingestion_file_has_tenant_checksum_idempotency_constraint() -> None:
    table = Base.metadata.tables["ingestion_files"]
    unique_constraints = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("tenant_id", "sha256") in unique_constraints
    assert table.c.tenant_id.foreign_keys
    assert table.c.sync_at.type.timezone is True
    assert table.c.observed_at.type.timezone is True
