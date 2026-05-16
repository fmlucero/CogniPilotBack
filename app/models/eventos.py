"""EventoApp / Incidente / Posicion — telemetría de la app móvil."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import TipoEvento

if TYPE_CHECKING:
    from app.models.operacion import Paquete
    from app.models.usuario import Dispositivo, Usuario


class EventoApp(Base):
    __tablename__ = "EventoApp"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tipo: Mapped[TipoEvento] = mapped_column(
        SAEnum(TipoEvento, name="TipoEvento", create_type=False, native_enum=True),
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    usuarioId: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("Usuario.id"), nullable=True
    )
    dispositivoId: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("Dispositivo.id"), nullable=True
    )
    inSchedule: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    screenName: Mapped[str | None] = mapped_column(String, nullable=True)
    appPackage: Mapped[str | None] = mapped_column(String, nullable=True)
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    screenText: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )

    # Relationships
    usuario: Mapped["Usuario | None"] = relationship(back_populates="eventos")
    dispositivo: Mapped["Dispositivo | None"] = relationship(back_populates="eventos")

    __table_args__ = (
        Index("EventoApp_ts_idx", "ts"),
        Index("EventoApp_dispositivoId_ts_idx", "dispositivoId", "ts"),
    )


class Incidente(Base):
    __tablename__ = "Incidente"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    repartidorId: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("Usuario.id"), nullable=False
    )
    dispositivoId: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("Dispositivo.id"), nullable=False
    )
    reglaId: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    paqueteId: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("Paquete.id"), nullable=True
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    datos: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    repartidor: Mapped["Usuario"] = relationship(back_populates="incidentes")
    dispositivo: Mapped["Dispositivo"] = relationship(back_populates="incidentes")
    paquete: Mapped["Paquete | None"] = relationship(back_populates="incidentes")

    __table_args__ = (Index("Incidente_repartidorId_ts_idx", "repartidorId", "ts"),)


class Posicion(Base):
    __tablename__ = "Posicion"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    repartidorId: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("Usuario.id"), nullable=False
    )
    dispositivoId: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("Dispositivo.id"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    lng: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)

    # Relationships
    repartidor: Mapped["Usuario"] = relationship(back_populates="posiciones")
    dispositivo: Mapped["Dispositivo"] = relationship(back_populates="posiciones")

    __table_args__ = (Index("Posicion_repartidorId_ts_idx", "repartidorId", "ts"),)
