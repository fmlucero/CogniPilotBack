"""HU-25: tabla ResetPasswordRequest

Revision ID: f25rstpw0001
Revises: e39setng0001
Create Date: 2026-05-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f25rstpw0001"
down_revision: str | Sequence[str] | None = "e39setng0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ResetPasswordRequest",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atendidaPor", sa.String(), nullable=True),
        sa.Column("atendidaAt", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["atendidaPor"], ["Usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ResetPasswordRequest_atendida_ts_idx", "ResetPasswordRequest", ["atendidaAt", "ts"])
    op.create_index("ResetPasswordRequest_email_ts_idx", "ResetPasswordRequest", ["email", "ts"])


def downgrade() -> None:
    op.drop_index("ResetPasswordRequest_email_ts_idx", table_name="ResetPasswordRequest")
    op.drop_index("ResetPasswordRequest_atendida_ts_idx", table_name="ResetPasswordRequest")
    op.drop_table("ResetPasswordRequest")
