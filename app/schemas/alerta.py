"""Schemas Pydantic v2 para alertas (HU-12)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AlertaResponse(BaseModel):
    id: str
    ts: int                       # ms epoch
    empresaId: str
    empresaNombre: str | None
    repartidorId: str | None
    repartidorNombre: str | None
    repartidorEmail: str | None
    tipo: str
    payload: dict[str, Any] | None
    leida: bool
    leidaPor: str | None
    leidaAt: int | None


class AlertasListResponse(BaseModel):
    alertas: list[AlertaResponse]
    unreadCount: int              # total no leídas en el scope del usuario
