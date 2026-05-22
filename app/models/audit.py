"""AuditEvent — HU-36: persistencia de eventos sensibles en DB.

Espejo en DB del log estructurado de HU-31. Los campos comunes
(actor/target/ip) son columnas indexables; el resto del payload queda en
`fields_json` (JSONB).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditEvent(Base):
    __tablename__ = "AuditEvent"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    event: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("Usuario.id"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String, nullable=True)
    target_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("Usuario.id"), nullable=True
    )
    target_email: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    fields_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("AuditEvent_ts_idx", "ts"),
        Index("AuditEvent_event_ts_idx", "event", "ts"),
        Index("AuditEvent_actor_ts_idx", "actor_id", "ts"),
    )
