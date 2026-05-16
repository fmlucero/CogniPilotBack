"""Schemas Pydantic v2 para eventos de la app móvil."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import TipoEvento


class EventCreateRequest(BaseModel):
    """POST /api/events — formato actual (singular) que manda la app Kotlin."""
    type: TipoEvento
    deviceUuid: str | None = None
    inSchedule: bool | None = None
    screenName: str | None = Field(default=None, max_length=120)
    appPackage: str | None = Field(default=None, max_length=120)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    screenText: list[str] = Field(default_factory=list, max_length=8)


class EventCompactResponse(BaseModel):
    """Salida en el formato compat con el admin web (timestamp en ms)."""
    id: str
    type: TipoEvento
    timestamp: int
    deviceId: str
    inSchedule: bool | None = None
    screenName: str | None = None
    appPackage: str | None = None
    keywords: list[str]
    screenText: list[str]


class EventsListResponse(BaseModel):
    events: list[EventCompactResponse]
    serverTime: int


class EventCreateResponse(BaseModel):
    ok: bool = True
    event: dict  # devolvemos el evento crudo (para compat con el back viejo)


# ─────────────────────────────────────────────────────────────────────────────
# Bulk ingestion — endpoint nuevo
# ─────────────────────────────────────────────────────────────────────────────


class BulkEventsRequest(BaseModel):
    """POST /api/events/bulk — ingesta en batch para alto throughput."""
    events: list[EventCreateRequest] = Field(min_length=1, max_length=500)


class BulkEventsResponse(BaseModel):
    accepted: int
    queuedJobId: str
