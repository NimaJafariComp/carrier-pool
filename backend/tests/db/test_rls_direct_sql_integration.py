"""Direct-SQL verification of PostgreSQL RLS as the non-owner app role."""

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Customer, Tenant
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.domain.types import SourceSystem

DATABASE_URL = os.getenv("DATABASE_URL")
APP_DATABASE_URL = os.getenv(
    "APP_DATABASE_URL",
    "postgresql+psycopg://carrier_pool_app:carrier_pool_app@localhost:5432/carrier_pool",
)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


def _now() -> datetime:
    return datetime(2026, 7, 27, tzinfo=UTC)


def _customer(tenant_id: UUID, suffix: str) -> Customer:
    return Customer(
        tenant_id=tenant_id,
        source_system=SourceSystem.FREIGHTFLOW,
        external_id=f"customer-{suffix}",
        name=f"Customer {suffix}",
        first_observed_at=_now(),
        last_observed_at=_now(),
    )


def test_app_role_rls_blocks_cross_tenant_direct_sql() -> None:
    assert DATABASE_URL is not None
    owner_engine = create_engine(DATABASE_URL)
    app_engine = create_engine(APP_DATABASE_URL)
    tenant_a_id, tenant_b_id = uuid4(), uuid4()

    try:
        with Session(owner_engine) as owner_session:
            owner_session.add_all(
                [
                    Tenant(
                        id=tenant_a_id,
                        slug=f"rls-a-{uuid4()}",
                        name="RLS A",
                        source_system=SourceSystem.FREIGHTFLOW,
                    ),
                    Tenant(
                        id=tenant_b_id,
                        slug=f"rls-b-{uuid4()}",
                        name="RLS B",
                        source_system=SourceSystem.FREIGHTFLOW,
                    ),
                ]
            )
            owner_session.commit()
            with owner_session.begin():
                set_tenant_context(owner_session, tenant_a_id)
                owner_session.add(_customer(tenant_a_id, "a"))
            with owner_session.begin():
                set_tenant_context(owner_session, tenant_b_id)
                owner_session.add(_customer(tenant_b_id, "b"))

        with Session(app_engine) as app_session:
            with app_session.begin():
                assert app_session.scalars(select(Customer.id)).all() == []
            with app_session.begin():
                set_tenant_context(app_session, tenant_a_id)
                visible_customer_ids = app_session.scalars(select(Customer.id)).all()
                assert len(visible_customer_ids) == 1
                assert (
                    app_session.execute(
                        delete(Customer).where(Customer.tenant_id == tenant_b_id)
                    ).rowcount
                    == 0
                )
    finally:
        owner_engine.dispose()
        app_engine.dispose()
