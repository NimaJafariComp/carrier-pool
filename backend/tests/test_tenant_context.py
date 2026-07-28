"""Tests for the trusted demo tenant-header boundary."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from carrier_pool.demo import DEMO_TENANTS
from carrier_pool.main import tenant_context


def test_tenant_context_accepts_only_server_authored_demo_bindings() -> None:
    assert tenant_context(str(DEMO_TENANTS[0].id)) == DEMO_TENANTS[0].id
    with pytest.raises(HTTPException, match="Invalid tenant context.") as error:
        tenant_context(str(uuid4()))
    assert error.value.status_code == 400
