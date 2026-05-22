"""Schemas Pydantic v2 para posiciones GPS."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field


# Lat ∈ [-90, 90], Lng ∈ [-180, 180]. La columna DB es Numeric(9, 6) (≈ 11cm de
# precisión); Pydantic acepta cualquier precisión y el router cuantiza a 6
# decimales antes del insert para no rechazar payloads de Doubles (Android,
# JavaScript) que vienen con 14+ dígitos significativos.
Latitude = Annotated[Decimal, Field(ge=-90, le=90)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180)]


class PositionReportRequest(BaseModel):
    deviceUuid: str
    lat: Latitude
    lng: Longitude
    ts: int | None = None  # ms epoch (opcional — si no viene, usa now())


class PositionBulkRequest(BaseModel):
    deviceUuid: str
    points: list["PositionPoint"] = Field(min_length=1, max_length=1000)


class PositionPoint(BaseModel):
    lat: Latitude
    lng: Longitude
    ts: int  # ms epoch


PositionBulkRequest.model_rebuild()


class PositionReportResponse(BaseModel):
    inserted: bool      # True si difería del último (se insertó); False si solo se actualizó lastSeen/lastLat/Lng
    queuedJobId: str | None = None


# ─── HU-11 — mapa de flota ──────────────────────────────────────────────────


class FleetPosition(BaseModel):
    """Última posición conocida de un repartidor (por dispositivo más activo)."""
    usuarioId: str
    usuarioNombre: str
    usuarioEmail: str
    empresaId: str | None
    empresaNombre: str | None
    dispositivoId: str
    deviceUuid: str
    lat: float
    lng: float
    lastSeen: int                 # ms epoch
    connectionState: str          # online | active_today | offline


class FleetPositionsResponse(BaseModel):
    positions: list[FleetPosition]
    serverTime: int               # ms epoch
