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
    # Modo exploración (captura de estructura de SC Pack) manda hasta ~70 líneas
    # por snapshot; el resto de los eventos siguen mandando ≤8. El límite acota
    # el payload sin recortar una captura de pantalla completa.
    screenText: list[str] = Field(default_factory=list, max_length=80)


class EventCompactResponse(BaseModel):
    """Salida en el formato compat con el admin web (timestamp en ms).

    HU-29: agregamos `usuario*` y `empresa*` para que el feed muestre quién
    hizo cada evento. Para eventos históricos sin usuarioId (de antes de HU-03),
    los campos van en None y el front los pinta como "anónimo".
    """
    id: str
    type: TipoEvento
    timestamp: int
    deviceId: str
    inSchedule: bool | None = None
    screenName: str | None = None
    appPackage: str | None = None
    keywords: list[str]
    screenText: list[str]
    usuarioId: str | None = None
    usuarioEmail: str | None = None
    usuarioNombre: str | None = None
    empresaId: str | None = None
    empresaNombre: str | None = None


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
