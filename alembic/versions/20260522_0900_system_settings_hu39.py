"""HU-39: tabla SystemSetting (key/value/JSONB)

Revision ID: e39setng0001
Revises: d12a1ert0001
Create Date: 2026-05-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e39setng0001"
down_revision: str | Sequence[str] | None = "d12a1ert0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "SystemSetting",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedBy", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["updatedBy"], ["Usuario.id"]),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("SystemSetting")
