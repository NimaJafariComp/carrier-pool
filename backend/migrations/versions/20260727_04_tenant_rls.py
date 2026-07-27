"""Enforce tenant isolation with PostgreSQL roles and row-level security.

Revision ID: 20260727_04
Revises: 20260727_03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260727_04"
down_revision: str | Sequence[str] | None = "20260727_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
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
)
POLICY = "tenant_isolation"
EXPRESSION = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          CREATE ROLE carrier_pool_app LOGIN PASSWORD 'carrier_pool_app'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO carrier_pool_app")
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO carrier_pool_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table} USING ({EXPRESSION}) WITH CHECK ({EXPRESSION})"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM carrier_pool_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM carrier_pool_app")
    op.execute("DROP ROLE carrier_pool_app")
