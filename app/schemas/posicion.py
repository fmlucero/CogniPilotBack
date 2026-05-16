"""Schemas Pydantic v2 para posiciones GPS."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field


# Lat ∈ [-90, 90], Lng ∈ [-180, 180]. Decimal con 6 decimales (≈ 11cm).
Latitude = Annotated[Decimal, Field(ge=-90, le=90, max_digits=9, decimal_places=6)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180, max_digits=9, decimal_places=6)]


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
