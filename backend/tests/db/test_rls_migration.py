"""RLS migration contract tests."""

from pathlib import Path


def test_rls_migration_forces_all_tenant_owned_tables() -> None:
    contents = (
        Path(__file__).parents[2] / "migrations" / "versions" / "20260727_04_tenant_rls.py"
    ).read_text()
    assert "carrier_pool_app" in contents
    assert "NOBYPASSRLS" in contents
    assert "FORCE ROW LEVEL SECURITY" in contents
    assert "current_setting('app.tenant_id', true)" in contents
