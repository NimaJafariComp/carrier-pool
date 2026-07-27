"""Persist current-stop local geography enrichment.

Revision ID: 20260727_06
Revises: 20260727_05
Create Date: 2026-07-27 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_06"
down_revision: str | Sequence[str] | None = "20260727_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stops", sa.Column("metro_group", sa.String(), nullable=True))
    op.add_column("stops", sa.Column("geography_quality_flags", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("stops", "geography_quality_flags")
    op.drop_column("stops", "metro_group")
