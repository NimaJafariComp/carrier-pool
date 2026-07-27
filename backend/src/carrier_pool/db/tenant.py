"""Trusted transaction-local PostgreSQL tenant context."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def set_tenant_context(session: Session, tenant_id: UUID) -> None:
    """Bind tenant scope for current transaction before any tenant-owned query."""
    session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)}
    )
