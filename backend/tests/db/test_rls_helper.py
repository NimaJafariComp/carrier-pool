"""RLS tenant-context helper tests."""

from unittest.mock import Mock
from uuid import uuid4

from carrier_pool.db.tenant import set_tenant_context


def test_set_tenant_context_is_transaction_local() -> None:
    session = Mock()
    tenant_id = uuid4()

    set_tenant_context(session, tenant_id)

    statement = session.execute.call_args.args[0]
    assert "set_config" in statement.text
    assert session.execute.call_args.args[1] == {"tenant_id": str(tenant_id)}
