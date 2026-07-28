"""Contracts for the deterministic review-demo tenant boundary."""

from carrier_pool.demo import DEMO_TENANTS
from carrier_pool.domain.types import SourceSystem


def test_demo_tenants_are_the_three_configured_broker_source_bindings() -> None:
    assert [(tenant.slug, tenant.source_system) for tenant in DEMO_TENANTS] == [
        ("ff-broker", SourceSystem.FREIGHTFLOW),
        ("hd-broker", SourceSystem.HAULDESK),
        ("bo-broker", SourceSystem.BROKEROS),
    ]
    assert len({tenant.id for tenant in DEMO_TENANTS}) == 3
