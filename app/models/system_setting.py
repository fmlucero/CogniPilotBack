"""HU-39 — Settings globales del sistema editables sin redeploy.

Tabla key/value mínima: la metadata (tipo, label, descripción) vive en
`app/core/settings_catalog.py` para mantenerla en código (no en DB). De
esta forma agregamos un setting nuevo simplemente extendiendo el catálogo,
y el SEED al startup poblará la fila si todavía no existe.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SystemSetting(Base):
    __tablename__ = "SystemSetting"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updatedBy: Mapped[str | None] = mapped_column(
        String, ForeignKey("Usuario.id"), nullable=True
    )
