"""Schemas Pydantic v2 para auditoría — HU-36."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AuditEventRow(BaseModel):
    id: str
    ts: int  # ms epoch
    event: str
    actor_id: str | None
    actor_email: str | None
    target_id: str | None
    target_email: str | None
    ip: str | None
    fields: dict[str, Any] | None


class AuditListResponse(BaseModel):
    eventos: list[AuditEventRow]
    total: int          # total filtrado (sin limit/offset)
    limit: int
    offset: int
