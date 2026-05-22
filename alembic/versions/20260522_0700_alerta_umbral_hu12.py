"""HU-12: tabla Alerta + Empresa.umbralErroresJornada

Revision ID: d12a1ert0001
Revises: c42ge0fc01a3
Create Date: 2026-05-22

- Crea tabla `Alerta` con índices por (empresaId, leida, ts), (repartidorId, ts) y ts.
- Agrega `Empresa.umbralErroresJornada` int NOT NULL DEFAULT 3.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d12a1ert0001"
down_revision: str | Sequence[str] | None = "c42ge0fc01a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "Empresa",
        sa.Column(
            "umbralErroresJornada",
            sa.Integer(),
            server_default="3",
            nullable=False,
        ),
    )
    op.create_table(
        "Alerta",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("empresaId", sa.String(), nullable=False),
        sa.Column("repartidorId", sa.String(), nullable=True),
        sa.Column("tipo", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("leida", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("leidaPor", sa.String(), nullable=True),
        sa.Column("leidaAt", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["empresaId"], ["Empresa.id"]),
        sa.ForeignKeyConstraint(["repartidorId"], ["Usuario.id"]),
        sa.ForeignKeyConstraint(["leidaPor"], ["Usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("Alerta_empresaId_leida_ts_idx", "Alerta", ["empresaId", "leida", "ts"])
    op.create_index("Alerta_repartidorId_ts_idx", "Alerta", ["repartidorId", "ts"])
    op.create_index("Alerta_ts_idx", "Alerta", ["ts"])


def downgrade() -> None:
    op.drop_index("Alerta_ts_idx", table_name="Alerta")
    op.drop_index("Alerta_repartidorId_ts_idx", table_name="Alerta")
    op.drop_index("Alerta_empresaId_leida_ts_idx", table_name="Alerta")
    op.drop_table("Alerta")
    op.drop_column("Empresa", "umbralErroresJornada")
