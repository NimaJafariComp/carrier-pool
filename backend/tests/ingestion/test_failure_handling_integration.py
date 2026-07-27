"""Phase 7.2 transactional failure-record integration tests."""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    Customer,
    IngestionFile,
    IngestionStatus,
    Load,
    LoadVersion,
    Tenant,
)
from carrier_pool.domain.models import CanonicalCustomerSnapshot
from carrier_pool.domain.types import SourceSystem
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import FreightFlowIngestionCoordinator

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


class _FailAfterFirstLoadCoordinator(FreightFlowIngestionCoordinator):
    def __init__(self) -> None:
        self._customers_seen = 0

    def _customer(
        self, session: Session, value: CanonicalCustomerSnapshot, observed_at: datetime
    ) -> Customer:
        customer = super()._customer(session, value, observed_at)
        self._customers_seen += 1
        if self._customers_seen == 2:
            raise RuntimeError("forced persistence failure")
        return customer


def _tenant() -> Tenant:
    return Tenant(
        id=uuid4(),
        slug=f"failure-{uuid4()}",
        name="Failure Test",
        source_system=SourceSystem.FREIGHTFLOW,
    )


def _two_load_file() -> SourceFile:
    path = Path(__file__).parents[3] / "data" / "tms_a_freightflow" / "example_sync.jsonc"
    payload = json.loads(re.sub(r"//.*$", "", path.read_text(), flags=re.MULTILINE))
    second = json.loads(json.dumps(payload["loads"][0]))
    second["shipmentId"] = 127472398
    payload["loads"].append(second)
    return SourceFile(Path("2026-07-01T00-00_sync.json"), json.dumps(payload).encode())


def test_partial_persistence_rolls_back_and_records_sanitized_failure() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    tenant = _tenant()
    source_file = _two_load_file()
    context = TenantContext(str(tenant.id))
    try:
        with Session(engine) as session:
            session.add(tenant)
            session.commit()

            with pytest.raises(RuntimeError, match="forced persistence failure"):
                _FailAfterFirstLoadCoordinator().ingest(session, source_file, context)

            assert session.scalars(select(Load).where(Load.tenant_id == tenant.id)).all() == []
            assert (
                session.scalars(select(LoadVersion).where(LoadVersion.tenant_id == tenant.id)).all()
                == []
            )
            assert (
                session.scalars(select(Customer).where(Customer.tenant_id == tenant.id)).all() == []
            )
            failed = session.scalar(
                select(IngestionFile).where(
                    IngestionFile.tenant_id == tenant.id,
                    IngestionFile.status == IngestionStatus.FAILED,
                )
            )
            assert failed is not None
            assert failed.errors_count == 1
            assert failed.error_details == {"code": "INGESTION_FAILED", "category": "RuntimeError"}
            assert failed.raw_payload == {"failure": {"code": "INGESTION_FAILED"}}

            session.rollback()
            corrected = SourceFile(source_file.path, source_file.content + b"\n")
            assert (
                FreightFlowIngestionCoordinator()
                .ingest(session, corrected, context)
                .versions_created
                == 2
            )
            statuses = session.scalars(
                select(IngestionFile.status).where(IngestionFile.tenant_id == tenant.id)
            ).all()
            assert sorted(statuses) == [IngestionStatus.COMPLETED, IngestionStatus.FAILED]
    finally:
        engine.dispose()


def test_fatal_parse_failure_records_source_validation_without_payload_leakage() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    tenant = _tenant()
    try:
        with Session(engine) as session:
            session.add(tenant)
            session.commit()

            with pytest.raises(ValueError):
                FreightFlowIngestionCoordinator().ingest(
                    session,
                    SourceFile(Path("2026-07-01T00-00_sync.json"), b"{not valid json"),
                    TenantContext(str(tenant.id)),
                )

            failed = session.scalar(
                select(IngestionFile).where(
                    IngestionFile.tenant_id == tenant.id,
                    IngestionFile.status == IngestionStatus.FAILED,
                )
            )
            assert failed is not None
            assert failed.error_details == {
                "code": "SOURCE_VALIDATION_FAILED",
                "category": "InvalidSourceFileError",
            }
            assert failed.raw_payload == {"failure": {"code": "SOURCE_VALIDATION_FAILED"}}
    finally:
        engine.dispose()
