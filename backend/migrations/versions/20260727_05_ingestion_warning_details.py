"""Add structured ingestion anomaly warnings.

Revision ID: 20260727_05
Revises: 20260727_04
Create Date: 2026-07-27 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_05"
down_revision: str | Sequence[str] | None = "20260727_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_files", sa.Column("warning_details", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ingestion_files", "warning_details")
