"""Schemas Pydantic v2 para CRUD de rutas (HU-50).

Una ruta tiene N paradas ordenadas; cada parada tiene lat/lng (geocerca futura,
HU-51), ventana horaria opcional y N paquetes. El editor del panel manda toda la
estructura anidada en una sola operación; PATCH con `paradas` reemplaza el set.
"""
from __future__ import annotations

from datetime import date as Date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# Las ventanas horarias son strings "HH:MM" (heredado de Prisma). Validamos formato.
_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


# ─────────────────────────────────────────────────────────────────────────────
# Input (crear / editar)
# ─────────────────────────────────────────────────────────────────────────────


class PaqueteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codigoMl: str = Field(min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=500)


class ParadaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orden: int = Field(ge=0, le=999)
    lat: Decimal = Field(ge=-90, le=90)
    lng: Decimal = Field(ge=-180, le=180)
    direccion: str | None = Field(default=None, max_length=300)
    ventanaDesde: str | None = Field(default=None, pattern=_HHMM)
    ventanaHasta: str | None = Field(default=None, pattern=_HHMM)
    paquetes: list[PaqueteIn] = Field(default_factory=list)


class RutaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    empresaId: str
    nombre: str = Field(min_length=1, max_length=200)
    fecha: Date
    paradas: list[ParadaIn] = Field(default_factory=list)


class RutaUpdate(BaseModel):
    """Campos no enviados no se tocan. Si `paradas` viene, reemplaza TODO el set."""
    model_config = ConfigDict(extra="forbid")
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    fecha: Date | None = None
    paradas: list[ParadaIn] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────


class PaqueteResponse(BaseModel):
    id: str
    codigoMl: str
    descripcion: str | None = None


class ParadaResponse(BaseModel):
    id: str
    orden: int
    lat: Decimal
    lng: Decimal
    direccion: str | None = None
    ventanaDesde: str | None = None
    ventanaHasta: str | None = None
    paquetes: list[PaqueteResponse]


class RutaResponse(BaseModel):
    """Detalle completo de una ruta con sus paradas y paquetes."""
    id: str
    empresaId: str
    empresaNombre: str | None = None
    nombre: str
    fecha: Date
    paradas: list[ParadaResponse]
    paquetesCount: int


class RutaListItem(BaseModel):
    """Item del listado — sin paradas anidadas, sólo contadores."""
    id: str
    empresaId: str
    empresaNombre: str | None = None
    nombre: str
    fecha: Date
    paradasCount: int
    paquetesCount: int
    asignacionesCount: int


class RutasListResponse(BaseModel):
    rutas: list[RutaListItem]
