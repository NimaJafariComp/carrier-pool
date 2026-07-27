"""Operational reporting for ingestion commands and structured logs."""

import json
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import IngestionFile, IngestionStatus
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.domain.types import SourceSystem

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """Stable operational fields for one attempted source file."""

    tenant: str
    source: str
    filename: str
    checksum: str
    records_seen: int
    versions_inserted: int
    projections_updated: int
    warnings: int
    errors: int
    no_op: bool

    @classmethod
    def from_file(cls, ingestion: IngestionFile, *, no_op: bool) -> "IngestionReport":
        return cls(
            tenant=str(ingestion.tenant_id),
            source=ingestion.source_system.value,
            filename=ingestion.file_name,
            checksum=ingestion.sha256,
            records_seen=ingestion.loads_seen,
            versions_inserted=ingestion.versions_created,
            projections_updated=ingestion.projections_updated,
            warnings=ingestion.warnings_count,
            errors=ingestion.errors_count,
            no_op=no_op,
        )

    def payload(self) -> dict[str, str | int | bool]:
        return {
            "tenant": self.tenant,
            "source": self.source,
            "filename": self.filename,
            "checksum": self.checksum,
            "records_seen": self.records_seen,
            "versions_inserted": self.versions_inserted,
            "projections_updated": self.projections_updated,
            "warnings": self.warnings,
            "errors": self.errors,
            "no_op": self.no_op,
        }

    def render(self) -> str:
        return json.dumps(self.payload(), sort_keys=True)

    def log(self, *, failed: bool = False) -> None:
        (logger.error if failed else logger.info)("ingestion %s", self.render())


def ingestion_summary(session: Session, tenant_id: UUID) -> dict[str, object]:
    """Return compact per-source ingestion totals for one trusted tenant."""
    with session.begin():
        set_tenant_context(session, tenant_id)
        rows = session.execute(
            select(
                IngestionFile.source_system,
                IngestionFile.status,
                func.count(),
                func.coalesce(func.sum(IngestionFile.loads_seen), 0),
                func.coalesce(func.sum(IngestionFile.versions_created), 0),
                func.coalesce(func.sum(IngestionFile.projections_updated), 0),
                func.coalesce(func.sum(IngestionFile.warnings_count), 0),
                func.coalesce(func.sum(IngestionFile.errors_count), 0),
            )
            .where(IngestionFile.tenant_id == tenant_id)
            .group_by(IngestionFile.source_system, IngestionFile.status)
            .order_by(IngestionFile.source_system, IngestionFile.status)
        ).all()
    return {
        "tenant": str(tenant_id),
        "files": [
            {
                "source": SourceSystem(source).value,
                "status": IngestionStatus(status).value,
                "files": count,
                "records_seen": records_seen,
                "versions_inserted": versions_inserted,
                "projections_updated": projections_updated,
                "warnings": warnings,
                "errors": errors,
            }
            for (
                source,
                status,
                count,
                records_seen,
                versions_inserted,
                projections_updated,
                warnings,
                errors,
            ) in rows
        ],
    }
