"""Create source ledger and persisted decision evidence tables.

Revision ID: 20260727_03
Revises: 20260727_02
Create Date: 2026-07-27 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_03"
down_revision: str | Sequence[str] | None = "20260727_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

source_system = postgresql.ENUM(
    "FREIGHTFLOW", "HAULDESK", "BROKEROS", name="source_system", create_type=False
)
financial_side = postgresql.ENUM("BILL", "PAY", name="financial_side", create_type=False)


def upgrade() -> None:
    financial_side.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "source_rate_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("load_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", source_system, nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("side", financial_side, nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"]),
        sa.ForeignKeyConstraint(["ingestion_file_id"], ["ingestion_files.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_source_rate_entries_identity"
        ),
    )
    op.create_index(
        "ix_source_rate_entries_tenant_load_observed_side",
        "source_rate_entries",
        ["tenant_id", "load_id", "observed_at", "side"],
    )
    op.create_table(
        "decision_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("load_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ranking_model_version", sa.String(), nullable=False),
        sa.Column("pricing_model_version", sa.String(), nullable=False),
        sa.Column("model_parameters", postgresql.JSONB(), nullable=False),
        sa.Column("price_estimate", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_summary", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"]),
        sa.ForeignKeyConstraint(["input_version_id"], ["load_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_decision_runs_tenant_load_as_of", "decision_runs", ["tenant_id", "load_id", "as_of"]
    )
    op.create_index(
        "ix_decision_runs_tenant_input_version", "decision_runs", ["tenant_id", "input_version_id"]
    )
    op.create_table(
        "carrier_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("carrier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("raw_score", sa.Numeric(8, 4), nullable=False),
        sa.Column("adjusted_score", sa.Numeric(8, 4), nullable=False),
        sa.Column("confidence", sa.Numeric(8, 4), nullable=False),
        sa.Column("component_values", postgresql.JSONB(), nullable=False),
        sa.Column("explanation_reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("rank > 0", name="ck_carrier_recommendations_positive_rank"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["decision_run_id"], ["decision_runs.id"]),
        sa.ForeignKeyConstraint(["carrier_id"], ["carriers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "decision_run_id", "rank", name="uq_carrier_recommendations_rank"
        ),
        sa.UniqueConstraint(
            "tenant_id", "decision_run_id", "carrier_id", name="uq_carrier_recommendations_carrier"
        ),
    )
    op.create_index(
        "ix_carrier_recommendations_tenant_run_rank",
        "carrier_recommendations",
        ["tenant_id", "decision_run_id", "rank"],
    )
    op.execute(
        "CREATE FUNCTION prevent_decision_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'Immutable decision rows cannot be updated or deleted' "
        "USING ERRCODE = 'check_violation'; END; $$ LANGUAGE plpgsql"
    )
    for table in ("source_rate_entries", "decision_runs", "carrier_recommendations"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_decision_mutation()"
        )


def downgrade() -> None:
    for table in ("carrier_recommendations", "decision_runs", "source_rate_entries"):
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
    op.execute("DROP FUNCTION prevent_decision_mutation()")
    op.drop_index(
        "ix_carrier_recommendations_tenant_run_rank", table_name="carrier_recommendations"
    )
    op.drop_table("carrier_recommendations")
    op.drop_index("ix_decision_runs_tenant_input_version", table_name="decision_runs")
    op.drop_index("ix_decision_runs_tenant_load_as_of", table_name="decision_runs")
    op.drop_table("decision_runs")
    op.drop_index(
        "ix_source_rate_entries_tenant_load_observed_side", table_name="source_rate_entries"
    )
    op.drop_table("source_rate_entries")
    financial_side.drop(op.get_bind(), checkfirst=True)
