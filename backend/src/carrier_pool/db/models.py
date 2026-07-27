"""Phase 3 tenant and ingestion metadata persistence models."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from carrier_pool.db.base import Base
from carrier_pool.domain.types import EquipmentType, FinancialSide, LoadStatus, SourceSystem


class IngestionStatus(StrEnum):
    """Lifecycle states for one source file ingestion attempt."""

    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


source_system_enum = Enum(SourceSystem, name="source_system", native_enum=True)
ingestion_status_enum = Enum(IngestionStatus, name="ingestion_status", native_enum=True)
load_status_enum = Enum(LoadStatus, name="load_status", native_enum=True)
equipment_type_enum = Enum(EquipmentType, name="equipment_type", native_enum=True)
financial_side_enum = Enum(FinancialSide, name="financial_side", native_enum=True)


class Tenant(Base):
    """A broker tenant, independent from its configured source system."""

    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    source_system: Mapped[SourceSystem] = mapped_column(source_system_enum, nullable=False)
    pool_opt_in: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("timezone('utc', now())"),
        onupdate=text("timezone('utc', now())"),
    )

    ingestion_files: Mapped[list["IngestionFile"]] = relationship(back_populates="tenant")
    customers: Mapped[list["Customer"]] = relationship(back_populates="tenant")
    carriers: Mapped[list["Carrier"]] = relationship(back_populates="tenant")
    loads: Mapped[list["Load"]] = relationship(back_populates="tenant")
    carrier_versions: Mapped[list["CarrierVersion"]] = relationship(back_populates="tenant")
    load_versions: Mapped[list["LoadVersion"]] = relationship(back_populates="tenant")
    stops: Mapped[list["Stop"]] = relationship(back_populates="tenant")


class IngestionFile(Base):
    """Idempotent record of an observed source sync file."""

    __tablename__ = "ingestion_files"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sha256", name="uq_ingestion_files_tenant_sha256"),
        CheckConstraint(
            "loads_seen >= 0 AND versions_created >= 0 AND projections_updated >= 0 "
            "AND warnings_count >= 0 AND errors_count >= 0",
            name="ck_ingestion_files_nonnegative_counters",
        ),
        Index("ix_ingestion_files_tenant_source_sync", "tenant_id", "source_system", "sync_at"),
        Index("ix_ingestion_files_tenant_status_observed", "tenant_id", "status", "observed_at"),
        Index("ix_ingestion_files_tenant_file_name", "tenant_id", "file_name"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_system: Mapped[SourceSystem] = mapped_column(source_system_enum, nullable=False)
    relative_path: Mapped[str] = mapped_column(String, nullable=False)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[IngestionStatus] = mapped_column(ingestion_status_enum, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loads_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    versions_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    projections_updated: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    warnings_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    errors_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("timezone('utc', now())"),
        onupdate=text("timezone('utc', now())"),
    )

    tenant: Mapped[Tenant] = relationship(back_populates="ingestion_files")
    carrier_versions: Mapped[list["CarrierVersion"]] = relationship(back_populates="ingestion_file")
    load_versions: Mapped[list["LoadVersion"]] = relationship(back_populates="ingestion_file")


class Customer(Base):
    """Current tenant-local customer projection."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_customers_tenant_source_external"
        ),
        Index("ix_customers_tenant_name", "tenant_id", "name"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_system: Mapped[SourceSystem] = mapped_column(source_system_enum, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("timezone('utc', now())"),
        onupdate=text("timezone('utc', now())"),
    )

    tenant: Mapped[Tenant] = relationship(back_populates="customers")
    loads: Mapped[list["Load"]] = relationship(back_populates="customer")
    load_versions: Mapped[list["LoadVersion"]] = relationship(back_populates="customer")


class Carrier(Base):
    """Current tenant-local carrier projection."""

    __tablename__ = "carriers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_carriers_tenant_source_external"
        ),
        Index("ix_carriers_tenant_normalized_name", "tenant_id", "normalized_name"),
        Index(
            "ix_carriers_tenant_mc_number",
            "tenant_id",
            "mc_number",
            postgresql_where=text("mc_number IS NOT NULL"),
        ),
        Index(
            "ix_carriers_tenant_dot_number",
            "tenant_id",
            "dot_number",
            postgresql_where=text("dot_number IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_system: Mapped[SourceSystem] = mapped_column(source_system_enum, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String, nullable=False)
    mc_number: Mapped[str | None] = mapped_column(String)
    dot_number: Mapped[str | None] = mapped_column(String)
    phone_number: Mapped[str | None] = mapped_column(String)
    home_city: Mapped[str | None] = mapped_column(String)
    home_state: Mapped[str | None] = mapped_column(String(2))
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("timezone('utc', now())"),
        onupdate=text("timezone('utc', now())"),
    )

    tenant: Mapped[Tenant] = relationship(back_populates="carriers")
    versions: Mapped[list["CarrierVersion"]] = relationship(back_populates="carrier")
    loads: Mapped[list["Load"]] = relationship(back_populates="carrier")
    load_versions: Mapped[list["LoadVersion"]] = relationship(back_populates="carrier")


class CarrierVersion(Base):
    """Immutable observed carrier snapshot."""

    __tablename__ = "carrier_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "carrier_id", "snapshot_hash", name="uq_carrier_versions_snapshot"
        ),
        Index(
            "ix_carrier_versions_tenant_carrier_observed", "tenant_id", "carrier_id", "observed_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    carrier_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("carriers.id"), nullable=False
    )
    ingestion_file_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("ingestion_files.id"), nullable=False
    )
    source_modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("carrier_versions.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )

    carrier: Mapped[Carrier] = relationship(back_populates="versions", foreign_keys=[carrier_id])
    tenant: Mapped[Tenant] = relationship(back_populates="carrier_versions")
    ingestion_file: Mapped[IngestionFile] = relationship(back_populates="carrier_versions")
    supersedes: Mapped["CarrierVersion | None"] = relationship(remote_side=[id])


class Load(Base):
    """Current tenant-local load projection, rebuildable from immutable versions."""

    __tablename__ = "loads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_loads_tenant_source_external"
        ),
        Index("ix_loads_tenant_status_observed", "tenant_id", "status", "observed_at"),
        Index("ix_loads_tenant_carrier_status", "tenant_id", "carrier_id", "status"),
        Index("ix_loads_tenant_customer", "tenant_id", "customer_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_system: Mapped[SourceSystem] = mapped_column(source_system_enum, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    load_number: Mapped[str | None] = mapped_column(String)
    customer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    carrier_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("carriers.id")
    )
    status: Mapped[LoadStatus] = mapped_column(load_status_enum, nullable=False)
    equipment: Mapped[EquipmentType | None] = mapped_column(equipment_type_enum)
    customer_rate_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2, asdecimal=True))
    carrier_rate_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2, asdecimal=True))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    weight_lbs: Mapped[Decimal | None] = mapped_column(Numeric(14, 3, asdecimal=True))
    distance_miles: Mapped[Decimal | None] = mapped_column(Numeric(12, 3, asdecimal=True))
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("load_versions.id"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("timezone('utc', now())"),
        onupdate=text("timezone('utc', now())"),
    )

    tenant: Mapped[Tenant] = relationship(back_populates="loads")
    customer: Mapped[Customer] = relationship(back_populates="loads")
    carrier: Mapped[Carrier | None] = relationship(back_populates="loads")
    current_version: Mapped["LoadVersion | None"] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )
    versions: Mapped[list["LoadVersion"]] = relationship(
        back_populates="load", foreign_keys="LoadVersion.load_id"
    )
    stops: Mapped[list["Stop"]] = relationship(back_populates="load")


class LoadVersion(Base):
    """Immutable normalized load observation used for all as-of queries."""

    __tablename__ = "load_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "load_id", "snapshot_hash", name="uq_load_versions_snapshot"),
        Index("ix_load_versions_tenant_load_observed", "tenant_id", "load_id", "observed_at"),
        Index("ix_load_versions_tenant_observed", "tenant_id", "observed_at"),
        Index("ix_load_versions_tenant_status_observed", "tenant_id", "status", "observed_at"),
        Index("ix_load_versions_tenant_carrier_observed", "tenant_id", "carrier_id", "observed_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    load_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("loads.id"), nullable=False
    )
    ingestion_file_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("ingestion_files.id"), nullable=False
    )
    source_modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[LoadStatus] = mapped_column(load_status_enum, nullable=False)
    equipment: Mapped[EquipmentType | None] = mapped_column(equipment_type_enum)
    customer_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("customers.id")
    )
    carrier_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("carriers.id")
    )
    customer_rate_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2, asdecimal=True))
    carrier_rate_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2, asdecimal=True))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    weight_lbs: Mapped[Decimal | None] = mapped_column(Numeric(14, 3, asdecimal=True))
    distance_miles: Mapped[Decimal | None] = mapped_column(Numeric(12, 3, asdecimal=True))
    canonical_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("load_versions.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )

    load: Mapped[Load] = relationship(back_populates="versions", foreign_keys=[load_id])
    tenant: Mapped[Tenant] = relationship(back_populates="load_versions")
    ingestion_file: Mapped[IngestionFile] = relationship(back_populates="load_versions")
    customer: Mapped[Customer | None] = relationship(back_populates="load_versions")
    carrier: Mapped[Carrier | None] = relationship(back_populates="load_versions")
    supersedes: Mapped["LoadVersion | None"] = relationship(remote_side=[id])


class Stop(Base):
    """Current ordered stop projection for a load."""

    __tablename__ = "stops"
    __table_args__ = (
        UniqueConstraint("tenant_id", "load_id", "sequence", name="uq_stops_tenant_load_sequence"),
        Index("ix_stops_tenant_load_sequence", "tenant_id", "load_id", "sequence"),
        Index(
            "ix_stops_tenant_pickup_h3_fine",
            "tenant_id",
            "h3_fine",
            postgresql_where=text("is_pickup"),
        ),
        Index(
            "ix_stops_tenant_dropoff_h3_fine",
            "tenant_id",
            "h3_fine",
            postgresql_where=text("is_dropoff"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    load_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("loads.id"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    is_pickup: Mapped[bool] = mapped_column(nullable=False)
    is_dropoff: Mapped[bool] = mapped_column(nullable=False)
    facility_name: Mapped[str | None] = mapped_column(String)
    city: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    postal_code: Mapped[str] = mapped_column(String, nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6, asdecimal=True))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6, asdecimal=True))
    h3_fine: Mapped[str | None] = mapped_column(String)
    h3_coarse: Mapped[str | None] = mapped_column(String)
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("timezone('utc', now())"),
        onupdate=text("timezone('utc', now())"),
    )

    load: Mapped[Load] = relationship(back_populates="stops")
    tenant: Mapped[Tenant] = relationship(back_populates="stops")


class SourceRateEntry(Base):
    """Immutable source ledger fact; negative adjustments remain separate rows."""

    __tablename__ = "source_rate_entries"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_source_rate_entries_identity"
        ),
        Index(
            "ix_source_rate_entries_tenant_load_observed_side",
            "tenant_id",
            "load_id",
            "observed_at",
            "side",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    load_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("loads.id"), nullable=False
    )
    ingestion_file_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("ingestion_files.id"), nullable=False
    )
    source_system: Mapped[SourceSystem] = mapped_column(source_system_enum, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[FinancialSide] = mapped_column(financial_side_enum, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2, asdecimal=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )


class DecisionRun(Base):
    """Immutable persisted historical-fit decision input and output evidence."""

    __tablename__ = "decision_runs"
    __table_args__ = (
        Index("ix_decision_runs_tenant_load_as_of", "tenant_id", "load_id", "as_of"),
        Index("ix_decision_runs_tenant_input_version", "tenant_id", "input_version_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    load_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("loads.id"), nullable=False
    )
    input_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("load_versions.id"), nullable=False
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ranking_model_version: Mapped[str] = mapped_column(String, nullable=False)
    pricing_model_version: Mapped[str] = mapped_column(String, nullable=False)
    model_parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    price_estimate: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )


class CarrierRecommendation(Base):
    """Immutable ranked carrier row within one persisted decision run."""

    __tablename__ = "carrier_recommendations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "decision_run_id", "rank", name="uq_carrier_recommendations_rank"
        ),
        UniqueConstraint(
            "tenant_id", "decision_run_id", "carrier_id", name="uq_carrier_recommendations_carrier"
        ),
        CheckConstraint("rank > 0", name="ck_carrier_recommendations_positive_rank"),
        Index("ix_carrier_recommendations_tenant_run_rank", "tenant_id", "decision_run_id", "rank"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    decision_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("decision_runs.id"), nullable=False
    )
    carrier_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("carriers.id"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_score: Mapped[Decimal] = mapped_column(Numeric(8, 4, asdecimal=True), nullable=False)
    adjusted_score: Mapped[Decimal] = mapped_column(Numeric(8, 4, asdecimal=True), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(8, 4, asdecimal=True), nullable=False)
    component_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    explanation_reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("timezone('utc', now())")
    )
