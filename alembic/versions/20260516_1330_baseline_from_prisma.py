"""baseline: schema heredado de Prisma (cognipilot-remote)

Esta revisión NO ejecuta DDL. Marca el punto de partida para que Alembic tome
ownership del schema existente sin riesgo de recrear tablas.

Procedimiento aplicado (correr UNA VEZ por DB, no idempotente):
    docker compose exec back-api alembic stamp head

Cualquier cambio futuro al schema parte de esta revisión:
    docker compose exec back-api alembic revision --autogenerate -m "..."
    docker compose exec back-api alembic upgrade head

Revision ID: 5a1e1b850521
Revises:
Create Date: 2026-05-16
"""
from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "5a1e1b850521"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Baseline — nada que aplicar (el schema ya existe desde Prisma).
    pass


def downgrade() -> None:
    # No vamos a hacer downgrade del baseline.
    pass
