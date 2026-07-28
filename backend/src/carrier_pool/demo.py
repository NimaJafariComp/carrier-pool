"""Fixed public tenant bindings for the deterministic review demo."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Tenant
from carrier_pool.domain.types import SourceSystem


@dataclass(frozen=True, slots=True)
class DemoTenant:
    """One hand-authored broker/source binding exposed by the review UI."""

    id: UUID
    slug: str
    name: str
    source_system: SourceSystem


DEMO_TENANTS: tuple[DemoTenant, ...] = (
    DemoTenant(
        UUID("11111111-1111-4111-8111-111111111111"),
        "ff-broker",
        "North Star Freight",
        SourceSystem.FREIGHTFLOW,
    ),
    DemoTenant(
        UUID("22222222-2222-4222-8222-222222222222"),
        "hd-broker",
        "Alamo Brokerage",
        SourceSystem.HAULDESK,
    ),
    DemoTenant(
        UUID("33333333-3333-4333-8333-333333333333"),
        "bo-broker",
        "Gulf Bridge Logistics",
        SourceSystem.BROKEROS,
    ),
)
DEMO_TENANT_SLUGS = tuple(tenant.slug for tenant in DEMO_TENANTS)
DEMO_TENANT_IDS = frozenset(tenant.id for tenant in DEMO_TENANTS)


def seed_demo_tenants(session: Session) -> tuple[DemoTenant, ...]:
    """Create the fixed demo brokers once; reject incompatible existing slugs."""
    existing_by_slug = {
        tenant.slug: tenant
        for tenant in session.scalars(
            select(Tenant).where(Tenant.slug.in_(DEMO_TENANT_SLUGS))
        ).all()
    }
    for definition in DEMO_TENANTS:
        existing = existing_by_slug.get(definition.slug)
        if existing is None:
            session.add(
                Tenant(
                    id=definition.id,
                    slug=definition.slug,
                    name=definition.name,
                    source_system=definition.source_system,
                )
            )
            continue
        if (
            existing.id != definition.id
            or existing.name != definition.name
            or existing.source_system is not definition.source_system
        ):
            raise ValueError(f"demo tenant slug {definition.slug!r} has an incompatible binding")
    session.flush()
    return DEMO_TENANTS
