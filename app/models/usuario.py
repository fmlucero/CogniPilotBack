"""Usuario y Dispositivo — autenticación y vínculo con el celu del repartidor."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import Rol

if TYPE_CHECKING:
    from app.models.empresa import Empresa
    from app.models.eventos import EventoApp, Incidente, Posicion
    from app.models.operacion import Asignacion
    from app.models.regla import ReglaHistorial


class Usuario(Base):
    __tablename__ = "Usuario"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    empresaId: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("Empresa.id"), nullable=True
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    passwordHash: Mapped[str] = mapped_column(String, nullable=False)
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    rol: Mapped[Rol] = mapped_column(
        SAEnum(Rol, name="Rol", create_type=False, native_enum=True),
        nullable=False,
    )
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    empresa: Mapped["Empresa | None"] = relationship(back_populates="usuarios")
    dispositivos: Mapped[list["Dispositivo"]] = relationship(back_populates="usuario")
    asignaciones: Mapped[list["Asignacion"]] = relationship(back_populates="repartidor")
    eventos: Mapped[list["EventoApp"]] = relationship(back_populates="usuario")
    incidentes: Mapped[list["Incidente"]] = relationship(back_populates="repartidor")
    posiciones: Mapped[list["Posicion"]] = relationship(back_populates="repartidor")
    reglas_hist: Mapped[list["ReglaHistorial"]] = relationship(back_populates="usuario")

    __table_args__ = (Index("Usuario_empresaId_idx", "empresaId"),)


class Dispositivo(Base):
    __tablename__ = "Dispositivo"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    usuarioId: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("Usuario.id"), nullable=False
    )
    deviceUuid: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    fcmToken: Mapped[str | None] = mapped_column(String, nullable=True)
    modelo: Mapped[str | None] = mapped_column(String, nullable=True)
    osVersion: Mapped[str | None] = mapped_column(String, nullable=True)
    appVersion: Mapped[str | None] = mapped_column(String, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    lastLat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    lastLng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    lastSeen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    usuario: Mapped["Usuario"] = relationship(back_populates="dispositivos")
    eventos: Mapped[list["EventoApp"]] = relationship(back_populates="dispositivo")
    incidentes: Mapped[list["Incidente"]] = relationship(back_populates="dispositivo")
    posiciones: Mapped[list["Posicion"]] = relationship(back_populates="dispositivo")

    __table_args__ = (Index("Dispositivo_usuarioId_idx", "usuarioId"),)
