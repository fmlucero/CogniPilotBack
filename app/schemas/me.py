"""Schemas Pydantic v2 para endpoints /api/me/* — vista personalizada del repartidor."""
from __future__ import annotations

from datetime import date as Date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class PaqueteOut(BaseModel):
    id: str
    codigoMl: str
    descripcion: str | None = None


class ParadaOut(BaseModel):
    id: str
    orden: int
    lat: Decimal
    lng: Decimal
    direccion: str | None = None
    ventanaDesde: str | None = None
    ventanaHasta: str | None = None
    paquetes: list[PaqueteOut]


class RutaOut(BaseModel):
    id: str
    nombre: str
    fecha: Date
    empresaId: str


class MiRutaResponse(BaseModel):
    """Respuesta de GET /api/me/ruta."""
    ruta: RutaOut
    paradas: list[ParadaOut]


class ReglaOut(BaseModel):
    id: str
    nombre: str
    tipo: str
    accion: str
    condicion: dict[str, Any]
    activa: bool
    rutaId: str | None = None


class MisReglasResponse(BaseModel):
    """Respuesta de GET /api/me/reglas."""
    reglas: list[ReglaOut]


class ChangePasswordRequest(BaseModel):
    """Cambio de password propia (HU-24). Auth requerida; cualquier rol puede usarlo."""
    currentPassword: str
    newPassword: str
