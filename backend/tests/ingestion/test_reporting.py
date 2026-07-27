"""Reporting contract for file-ingestion CLI and logs."""

from datetime import UTC, datetime
from uuid import uuid4

from carrier_pool.db.models import IngestionFile, IngestionStatus
from carrier_pool.domain.types import SourceSystem
from carrier_pool.ingestion.reporting import IngestionReport


def test_ingestion_report_contains_required_operational_fields() -> None:
    ingestion = IngestionFile(
        tenant_id=uuid4(),
        source_system=SourceSystem.FREIGHTFLOW,
        relative_path="data/tms_a_freightflow",
        file_name="2026-07-01T00-00_sync.json",
        sha256="a" * 64,
        raw_payload={},
        sync_at=datetime(2026, 7, 1, tzinfo=UTC),
        observed_at=datetime(2026, 7, 1, tzinfo=UTC),
        started_at=datetime(2026, 7, 1, tzinfo=UTC),
        status=IngestionStatus.COMPLETED,
        loads_seen=2,
        versions_created=1,
        projections_updated=1,
        warnings_count=1,
        errors_count=0,
    )

    payload = IngestionReport.from_file(ingestion, no_op=False).payload()

    assert payload == {
        "tenant": str(ingestion.tenant_id),
        "source": "FREIGHTFLOW",
        "filename": "2026-07-01T00-00_sync.json",
        "checksum": "a" * 64,
        "records_seen": 2,
        "versions_inserted": 1,
        "projections_updated": 1,
        "warnings": 1,
        "errors": 0,
        "no_op": False,
    }
