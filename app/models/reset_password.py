"""HU-25 — Solicitudes de reset de password.

Sin email server por ahora: el usuario solicita desde /login, el admin ve
la lista pendiente en /solicitudes, dispara el reset y entrega el nuevo
password manualmente al usuario por canal seguro.

Notas:
- email se guarda como lo envió el usuario (lowercased en la lógica). NO
  validamos en este endpoint público si el usuario existe — devolvemos
  siempre 200 para no filtrar enumeración. La solicitud queda registrada
  igual; el admin la verá y decide qué hacer (un email inválido se
  puede marcar como atendida sin reset si no matchea ningún usuario).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ResetPasswordRequest(Base):
    __tablename__ = "ResetPasswordRequest"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atendidaPor: Mapped[str | None] = mapped_column(
        String, ForeignKey("Usuario.id"), nullable=True
    )
    atendidaAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ResetPasswordRequest_atendida_ts_idx", "atendidaAt", "ts"),
        Index("ResetPasswordRequest_email_ts_idx", "email", "ts"),
    )
