"""Ruta / Parada / Paquete / Asignacion — domínio de operación logística."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.empresa import Empresa
    from app.models.eventos import Incidente
    from app.models.regla import Regla
    from app.models.usuario import Usuario


class Ruta(Base):
    __tablename__ = "Ruta"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    empresaId: Mapped[str] = mapped_column(
        String, ForeignKey("Empresa.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)

    # Relationships
    empresa: Mapped["Empresa"] = relationship(back_populates="rutas")
    paradas: Mapped[list["Parada"]] = relationship(back_populates="ruta")
    reglas: Mapped[list["Regla"]] = relationship(back_populates="ruta")
    asignaciones: Mapped[list["Asignacion"]] = relationship(back_populates="ruta")

    __table_args__ = (Index("Ruta_empresaId_fecha_idx", "empresaId", "fecha"),)


class Parada(Base):
    __tablename__ = "Parada"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    rutaId: Mapped[str] = mapped_column(
        String, ForeignKey("Ruta.id"), nullable=False
    )
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    lng: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    direccion: Mapped[str | None] = mapped_column(String, nullable=True)
    ventanaDesde: Mapped[str | None] = mapped_column(String, nullable=True)
    ventanaHasta: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    ruta: Mapped["Ruta"] = relationship(back_populates="paradas")
    paquetes: Mapped[list["Paquete"]] = relationship(back_populates="parada")


class Paquete(Base):
    __tablename__ = "Paquete"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    paradaId: Mapped[str] = mapped_column(
        String, ForeignKey("Parada.id"), nullable=False
    )
    codigoMl: Mapped[str] = mapped_column(String, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    parada: Mapped["Parada"] = relationship(back_populates="paquetes")
    incidentes: Mapped[list["Incidente"]] = relationship(back_populates="paquete")

    __table_args__ = (Index("Paquete_codigoMl_idx", "codigoMl"),)


class Asignacion(Base):
    __tablename__ = "Asignacion"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    repartidorId: Mapped[str] = mapped_column(
        String, ForeignKey("Usuario.id"), nullable=False
    )
    rutaId: Mapped[str] = mapped_column(
        String, ForeignKey("Ruta.id"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)

    # Relationships
    repartidor: Mapped["Usuario"] = relationship(back_populates="asignaciones")
    ruta: Mapped["Ruta"] = relationship(back_populates="asignaciones")

    __table_args__ = (
        UniqueConstraint("repartidorId", "fecha", name="Asignacion_repartidorId_fecha_key"),
    )
