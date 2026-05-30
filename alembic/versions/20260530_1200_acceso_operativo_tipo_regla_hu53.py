"""HU-53: agregar valor 'acceso_operativo' al enum TipoRegla

Revision ID: a53acc0per01
Revises: f25rstpw0001
Create Date: 2026-05-30

ALTER TYPE ... ADD VALUE corre en autocommit_block porque algunos clientes
Postgres no aceptan ese DDL dentro de una transacción explícita. IF NOT EXISTS
hace la migración idempotente.

No hay downgrade: Postgres no permite DROP VALUE de un enum. Si hay que
revertir, hay que crear un tipo nuevo + USING en cada columna.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a53acc0per01"
down_revision: str | Sequence[str] | None = "f25rstpw0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE \"TipoRegla\" ADD VALUE IF NOT EXISTS 'acceso_operativo'")


def downgrade() -> None:
    pass
