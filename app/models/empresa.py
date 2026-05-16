"""Empresa — multitenancy raíz."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.operacion import Ruta
    from app.models.regla import Regla
    from app.models.usuario import Usuario


class Empresa(Base):
    __tablename__ = "Empresa"  # Prisma usa PascalCase para tablas

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    nombre: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    cuit: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    contacto: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="empresa")
    rutas: Mapped[list["Ruta"]] = relationship(back_populates="empresa")
    reglas: Mapped[list["Regla"]] = relationship(back_populates="empresa")
