"""Create tenant and ingestion metadata tables.

Revision ID: 20260727_01
Revises:
Create Date: 2026-07-27 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

source_system = postgresql.ENUM(
    "FREIGHTFLOW", "HAULDESK", "BROKEROS", name="source_system", create_type=False
)
ingestion_status = postgresql.ENUM(
    "PROCESSING", "COMPLETED", "FAILED", name="ingestion_status", create_type=False
)


def upgrade() -> None:
    source_system.create(op.get_bind(), checkfirst=True)
    ingestion_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("source_system", source_system, nullable=False),
        sa.Column("pool_opt_in", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_table(
        "ingestion_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", source_system, nullable=False),
        sa.Column("relative_path", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sync_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", ingestion_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loads_seen", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("versions_created", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("projections_updated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("warnings_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("errors_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "loads_seen >= 0 AND versions_created >= 0 AND projections_updated >= 0 "
            "AND warnings_count >= 0 AND errors_count >= 0",
            name="ck_ingestion_files_nonnegative_counters",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_ingestion_files_tenant_sha256"),
    )
    op.create_index(
        "ix_ingestion_files_tenant_source_sync",
        "ingestion_files",
        ["tenant_id", "source_system", "sync_at"],
    )
    op.create_index(
        "ix_ingestion_files_tenant_status_observed",
        "ingestion_files",
        ["tenant_id", "status", "observed_at"],
    )
    op.create_index(
        "ix_ingestion_files_tenant_file_name", "ingestion_files", ["tenant_id", "file_name"]
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_files_tenant_file_name", table_name="ingestion_files")
    op.drop_index("ix_ingestion_files_tenant_status_observed", table_name="ingestion_files")
    op.drop_index("ix_ingestion_files_tenant_source_sync", table_name="ingestion_files")
    op.drop_table("ingestion_files")
    op.drop_table("tenants")
    ingestion_status.drop(op.get_bind(), checkfirst=True)
    source_system.drop(op.get_bind(), checkfirst=True)
