"""Alerta — notificación generada por el back cuando se dispara una regla
operativa (HU-12 umbral de errores; futuras alertas tipo geofence trigger).

Persistir las alertas (vs sólo emitirlas por SSE) permite:
  - El supervisor que recién abre el panel ve las alertas pendientes sin perder
    nada que haya pasado mientras estaba off-line.
  - Auditoría: queda registro histórico de qué alertas se dispararon y cuándo
    se marcaron como leídas.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Alerta(Base):
    __tablename__ = "Alerta"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    empresaId: Mapped[str] = mapped_column(
        String, ForeignKey("Empresa.id"), nullable=False
    )
    repartidorId: Mapped[str | None] = mapped_column(
        String, ForeignKey("Usuario.id"), nullable=True
    )
    tipo: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    leida: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    leidaPor: Mapped[str | None] = mapped_column(
        String, ForeignKey("Usuario.id"), nullable=True
    )
    leidaAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("Alerta_empresaId_leida_ts_idx", "empresaId", "leida", "ts"),
        Index("Alerta_repartidorId_ts_idx", "repartidorId", "ts"),
        Index("Alerta_ts_idx", "ts"),
    )
