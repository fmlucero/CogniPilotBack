"""Regla y ReglaHistorial — motor de reglas configurable remotamente."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import AccionRegla, TipoRegla

if TYPE_CHECKING:
    from app.models.empresa import Empresa
    from app.models.operacion import Ruta
    from app.models.usuario import Usuario


class Regla(Base):
    __tablename__ = "Regla"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    empresaId: Mapped[str] = mapped_column(
        String, ForeignKey("Empresa.id"), nullable=False
    )
    rutaId: Mapped[str | None] = mapped_column(
        String, ForeignKey("Ruta.id"), nullable=True
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    tipo: Mapped[TipoRegla] = mapped_column(
        SAEnum(TipoRegla, name="TipoRegla", create_type=False, native_enum=True),
        nullable=False,
    )
    accion: Mapped[AccionRegla] = mapped_column(
        SAEnum(AccionRegla, name="AccionRegla", create_type=False, native_enum=True),
        nullable=False,
    )
    condicion: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # default Python además del server_default: la tabla heredada de Prisma no tiene
    # default en DB (Prisma maneja @updatedAt a nivel ORM), así que los INSERTs directos
    # de SQLAlchemy no obtienen el valor automáticamente. Ver I-20 en COGNIPILOT_STATUS.md.
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    # Relationships
    empresa: Mapped["Empresa"] = relationship(back_populates="reglas")
    ruta: Mapped["Ruta | None"] = relationship(back_populates="reglas")
    historial: Mapped[list["ReglaHistorial"]] = relationship(back_populates="regla")

    __table_args__ = (Index("Regla_empresaId_activa_idx", "empresaId", "activa"),)


class ReglaHistorial(Base):
    __tablename__ = "ReglaHistorial"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    reglaId: Mapped[str] = mapped_column(
        String, ForeignKey("Regla.id"), nullable=False
    )
    usuarioId: Mapped[str] = mapped_column(
        String, ForeignKey("Usuario.id"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    campo: Mapped[str] = mapped_column(String, nullable=False)
    valorOld: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    valorNew: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    regla: Mapped["Regla"] = relationship(back_populates="historial")
    usuario: Mapped["Usuario"] = relationship(back_populates="reglas_hist")

    __table_args__ = (Index("ReglaHistorial_reglaId_ts_idx", "reglaId", "ts"),)
