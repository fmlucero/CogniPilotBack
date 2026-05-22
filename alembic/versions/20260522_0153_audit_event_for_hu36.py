"""HU-36: tabla AuditEvent para auditoria persistida

Revision ID: a36e1a36c001
Revises: 5a1e1b850521
Create Date: 2026-05-22

Solo crea la tabla nueva. Ignoramos a propósito los diffs cosméticos
(TEXT vs String, type repaints, recreate de FKs) que autogenerate detecta
contra el schema Prisma original — la DB ya está como debe estar y esos
diffs son ruido entre dos formas de declarar lo mismo.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a36e1a36c001"
down_revision: str | Sequence[str] | None = "5a1e1b850521"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "AuditEvent",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("actor_email", sa.String(), nullable=True),
        sa.Column("target_id", sa.String(), nullable=True),
        sa.Column("target_email", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("fields_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["Usuario.id"]),
        sa.ForeignKeyConstraint(["target_id"], ["Usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("AuditEvent_ts_idx", "AuditEvent", ["ts"])
    op.create_index("AuditEvent_event_ts_idx", "AuditEvent", ["event", "ts"])
    op.create_index("AuditEvent_actor_ts_idx", "AuditEvent", ["actor_id", "ts"])


def downgrade() -> None:
    op.drop_index("AuditEvent_actor_ts_idx", table_name="AuditEvent")
    op.drop_index("AuditEvent_event_ts_idx", table_name="AuditEvent")
    op.drop_index("AuditEvent_ts_idx", table_name="AuditEvent")
    op.drop_table("AuditEvent")
