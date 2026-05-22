"""HU-36 — Auditoría persistida.

GET /api/auditoria — admin_sistema only. Filtros:
    from, to           (ISO8601 o ms epoch)
    event              (1+ comma-separated)
    actor              (substring case-insensitive contra actor_email)
    include_telemetry  (default false: oculta event_ingested / events_bulk_ingested / position_reported)
    limit              (default 100, max 500)
    offset             (default 0)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import TELEMETRY_EVENTS
from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.audit import AuditEvent
from app.schemas.audit import AuditEventRow, AuditListResponse

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])


def _parse_ts(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    # Acepta ms epoch como string o ISO8601.
    if value.isdigit():
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"timestamp inválido: {value}") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("", response_model=AuditListResponse)
async def list_auditoria(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: Annotated[str | None, Query()] = None,
    event: Annotated[str | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
    include_telemetry: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditListResponse:
    if current["rol"] != "admin_sistema":
        raise HTTPException(status_code=403, detail="Forbidden")

    base = select(AuditEvent)
    count_base = select(func.count(AuditEvent.id))

    def _apply_filters(stmt):
        if (dt := _parse_ts(from_)) is not None:
            stmt = stmt.where(AuditEvent.ts >= dt)
        if (dt := _parse_ts(to)) is not None:
            stmt = stmt.where(AuditEvent.ts <= dt)
        if event:
            wanted = [e.strip() for e in event.split(",") if e.strip()]
            if wanted:
                stmt = stmt.where(AuditEvent.event.in_(wanted))
        elif not include_telemetry:
            # Sin filtro explícito por event, ocultamos los de alto volumen.
            stmt = stmt.where(AuditEvent.event.notin_(TELEMETRY_EVENTS))
        if actor:
            needle = f"%{actor.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(AuditEvent.actor_email).like(needle),
                    func.lower(AuditEvent.target_email).like(needle),
                )
            )
        return stmt

    total = (await db.execute(_apply_filters(count_base))).scalar_one()

    stmt = _apply_filters(base).order_by(AuditEvent.ts.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()

    return AuditListResponse(
        eventos=[
            AuditEventRow(
                id=r.id,
                ts=int(_aware_utc(r.ts).timestamp() * 1000),
                event=r.event,
                actor_id=r.actor_id,
                actor_email=r.actor_email,
                target_id=r.target_id,
                target_email=r.target_email,
                ip=r.ip,
                fields=r.fields_json,
            )
            for r in rows
        ],
        total=int(total),
        limit=limit,
        offset=offset,
    )
