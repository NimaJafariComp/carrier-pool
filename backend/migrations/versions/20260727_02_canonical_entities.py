"""Create canonical entity, version, and current-stop tables.

Revision ID: 20260727_02
Revises: 20260727_01
Create Date: 2026-07-27 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_02"
down_revision: str | Sequence[str] | None = "20260727_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

source_system = postgresql.ENUM(
    "FREIGHTFLOW", "HAULDESK", "BROKEROS", name="source_system", create_type=False
)
load_status = postgresql.ENUM(
    "PLANNED",
    "ACTIVE",
    "COVERED",
    "IN_TRANSIT",
    "DELIVERED",
    "COMPLETED",
    name="load_status",
    create_type=False,
)
equipment_type = postgresql.ENUM(
    "DRY_VAN", "REEFER", "FLATBED", "UNKNOWN", name="equipment_type", create_type=False
)


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
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
    ]


def upgrade() -> None:
    load_status.create(op.get_bind(), checkfirst=True)
    equipment_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", source_system, nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_customers_tenant_source_external"
        ),
    )
    op.create_index("ix_customers_tenant_name", "customers", ["tenant_id", "name"])

    op.create_table(
        "carriers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", source_system, nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        sa.Column("mc_number", sa.String(), nullable=True),
        sa.Column("dot_number", sa.String(), nullable=True),
        sa.Column("phone_number", sa.String(), nullable=True),
        sa.Column("home_city", sa.String(), nullable=True),
        sa.Column("home_state", sa.String(length=2), nullable=True),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_carriers_tenant_source_external"
        ),
    )
    op.create_index(
        "ix_carriers_tenant_normalized_name", "carriers", ["tenant_id", "normalized_name"]
    )
    op.create_index(
        "ix_carriers_tenant_mc_number",
        "carriers",
        ["tenant_id", "mc_number"],
        postgresql_where=sa.text("mc_number IS NOT NULL"),
    )
    op.create_index(
        "ix_carriers_tenant_dot_number",
        "carriers",
        ["tenant_id", "dot_number"],
        postgresql_where=sa.text("dot_number IS NOT NULL"),
    )

    op.create_table(
        "carrier_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("carrier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("canonical_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["carrier_id"], ["carriers.id"]),
        sa.ForeignKeyConstraint(["ingestion_file_id"], ["ingestion_files.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["carrier_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "carrier_id", "snapshot_hash", name="uq_carrier_versions_snapshot"
        ),
    )
    op.create_index(
        "ix_carrier_versions_tenant_carrier_observed",
        "carrier_versions",
        ["tenant_id", "carrier_id", sa.text("observed_at DESC")],
    )

    op.create_table(
        "loads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", source_system, nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("load_number", sa.String(), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("carrier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", load_status, nullable=False),
        sa.Column("equipment", equipment_type, nullable=True),
        sa.Column("customer_rate_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("carrier_rate_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("weight_lbs", sa.Numeric(precision=14, scale=3), nullable=True),
        sa.Column("distance_miles", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["carrier_id"], ["carriers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_loads_tenant_source_external"
        ),
        sa.UniqueConstraint("current_version_id", name="uq_loads_current_version"),
    )
    op.create_index(
        "ix_loads_tenant_status_observed", "loads", ["tenant_id", "status", "observed_at"]
    )
    op.create_index(
        "ix_loads_tenant_carrier_status", "loads", ["tenant_id", "carrier_id", "status"]
    )
    op.create_index("ix_loads_tenant_customer", "loads", ["tenant_id", "customer_id"])

    op.create_table(
        "load_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("load_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", load_status, nullable=False),
        sa.Column("equipment", equipment_type, nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("carrier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_rate_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("carrier_rate_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("weight_lbs", sa.Numeric(precision=14, scale=3), nullable=True),
        sa.Column("distance_miles", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("canonical_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"]),
        sa.ForeignKeyConstraint(["ingestion_file_id"], ["ingestion_files.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["carrier_id"], ["carriers.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["load_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "load_id", "snapshot_hash", name="uq_load_versions_snapshot"
        ),
    )
    op.create_foreign_key(
        "fk_loads_current_version", "loads", "load_versions", ["current_version_id"], ["id"]
    )
    op.create_index(
        "ix_load_versions_tenant_load_observed",
        "load_versions",
        ["tenant_id", "load_id", sa.text("observed_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_load_versions_tenant_observed",
        "load_versions",
        ["tenant_id", sa.text("observed_at DESC")],
    )
    op.create_index(
        "ix_load_versions_tenant_status_observed",
        "load_versions",
        ["tenant_id", "status", sa.text("observed_at DESC")],
    )
    op.create_index(
        "ix_load_versions_tenant_carrier_observed",
        "load_versions",
        ["tenant_id", "carrier_id", sa.text("observed_at DESC")],
    )

    op.create_table(
        "stops",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("load_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("is_pickup", sa.Boolean(), nullable=False),
        sa.Column("is_dropoff", sa.Boolean(), nullable=False),
        sa.Column("facility_name", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=False),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("postal_code", sa.String(), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("h3_fine", sa.String(), nullable=True),
        sa.Column("h3_coarse", sa.String(), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_arrival_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_departure_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "load_id", "sequence", name="uq_stops_tenant_load_sequence"
        ),
    )
    op.create_index("ix_stops_tenant_load_sequence", "stops", ["tenant_id", "load_id", "sequence"])
    op.create_index(
        "ix_stops_tenant_pickup_h3_fine",
        "stops",
        ["tenant_id", "h3_fine"],
        postgresql_where=sa.text("is_pickup"),
    )
    op.create_index(
        "ix_stops_tenant_dropoff_h3_fine",
        "stops",
        ["tenant_id", "h3_fine"],
        postgresql_where=sa.text("is_dropoff"),
    )

    op.execute(
        """
        CREATE FUNCTION prevent_version_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Immutable version rows cannot be updated or deleted'
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER carrier_versions_immutable BEFORE UPDATE OR DELETE ON carrier_versions "
        "FOR EACH ROW EXECUTE FUNCTION prevent_version_mutation()"
    )
    op.execute(
        "CREATE TRIGGER load_versions_immutable BEFORE UPDATE OR DELETE ON load_versions "
        "FOR EACH ROW EXECUTE FUNCTION prevent_version_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER load_versions_immutable ON load_versions")
    op.execute("DROP TRIGGER carrier_versions_immutable ON carrier_versions")
    op.execute("DROP FUNCTION prevent_version_mutation()")
    op.drop_index("ix_stops_tenant_dropoff_h3_fine", table_name="stops")
    op.drop_index("ix_stops_tenant_pickup_h3_fine", table_name="stops")
    op.drop_index("ix_stops_tenant_load_sequence", table_name="stops")
    op.drop_table("stops")
    op.drop_index("ix_load_versions_tenant_carrier_observed", table_name="load_versions")
    op.drop_index("ix_load_versions_tenant_status_observed", table_name="load_versions")
    op.drop_index("ix_load_versions_tenant_observed", table_name="load_versions")
    op.drop_index("ix_load_versions_tenant_load_observed", table_name="load_versions")
    op.drop_constraint("fk_loads_current_version", "loads", type_="foreignkey")
    op.drop_table("load_versions")
    op.drop_index("ix_loads_tenant_customer", table_name="loads")
    op.drop_index("ix_loads_tenant_carrier_status", table_name="loads")
    op.drop_index("ix_loads_tenant_status_observed", table_name="loads")
    op.drop_table("loads")
    op.drop_index("ix_carrier_versions_tenant_carrier_observed", table_name="carrier_versions")
    op.drop_table("carrier_versions")
    op.drop_index("ix_carriers_tenant_dot_number", table_name="carriers")
    op.drop_index("ix_carriers_tenant_mc_number", table_name="carriers")
    op.drop_index("ix_carriers_tenant_normalized_name", table_name="carriers")
    op.drop_table("carriers")
    op.drop_index("ix_customers_tenant_name", table_name="customers")
    op.drop_table("customers")
    equipment_type.drop(op.get_bind(), checkfirst=True)
    load_status.drop(op.get_bind(), checkfirst=True)
