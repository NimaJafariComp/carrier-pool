"""Allow the application role to read safe demo tenant selection fields.

Revision ID: 20260727_07
Revises: 20260727_06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260727_07"
down_revision: str | Sequence[str] | None = "20260727_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON TABLE tenants TO carrier_pool_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE tenants FROM carrier_pool_app")
