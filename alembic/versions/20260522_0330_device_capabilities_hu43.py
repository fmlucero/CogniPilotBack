"""HU-43: Dispositivo.capabilities (JSONB) + capabilities_updated_at

Revision ID: b43d10c1a002
Revises: a36e1a36c001
Create Date: 2026-05-22

Solo agrega columnas nuevas (nullable, sin default) — operación segura, sin
backfill: una columna nullable nueva en Postgres es O(1) sin rewrite de tabla.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b43d10c1a002"
down_revision: str | Sequence[str] | None = "a36e1a36c001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "Dispositivo",
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "Dispositivo",
        sa.Column("capabilities_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("Dispositivo", "capabilities_updated_at")
    op.drop_column("Dispositivo", "capabilities")
