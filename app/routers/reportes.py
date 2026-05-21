"""HU-16 — Exportación de reportes en CSV.

Endpoint GET /api/reportes/eventos.csv que streamea CSV con los eventos
filtrados. Scope por rol:
  - admin_sistema: todo, puede pasar ?empresaId=
  - supervisor/gerente: forzado a su empresa
  - repartidor: 403

Diseño: una sola query con JOIN a Usuario + Empresa + Dispositivo, ordenada
por ts ASC. Streaming response (StreamingResponse + generator) para no
cargar todo en memoria. Límite hard de 50k filas para evitar abuso.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.empresa import Empresa
from app.models.eventos import EventoApp
from app.models.usuario import Dispositivo, Usuario

router = APIRouter(prefix="/api/reportes", tags=["reportes"])

MAX_ROWS = 50_000

CSV_HEADERS = [
    "fecha_iso",
    "usuario_email",
    "usuario_nombre",
    "empresa",
    "tipo",
    "in_schedule",
    "screen_name",
    "app_package",
    "device_uuid",
]


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


@router.get("/eventos.csv")
async def export_eventos_csv(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[int | None, Query(alias="from")] = None,
    to: Annotated[int | None, Query()] = None,
    empresaId: Annotated[str | None, Query()] = None,
    usuarioId: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    rol = current["rol"]
    if rol == "repartidor":
        raise HTTPException(status_code=403, detail="Forbidden")

    now = datetime.now(timezone.utc)
    range_to = _ms_to_dt(to) if to else now
    range_from = _ms_to_dt(from_) if from_ else (range_to - timedelta(days=7))
    if range_from > range_to:
        raise HTTPException(status_code=422, detail="`from` debe ser anterior a `to`")

    # Scope
    empresa_scope: str | None = None
    if rol == "admin_sistema":
        empresa_scope = empresaId
    else:
        if not current["empresaId"]:
            raise HTTPException(status_code=403, detail="Usuario sin empresa asignada")
        if empresaId and empresaId != current["empresaId"]:
            raise HTTPException(status_code=403, detail="Solo podés exportar tu propia empresa")
        empresa_scope = current["empresaId"]

    stmt = (
        select(EventoApp)
        .options(
            selectinload(EventoApp.usuario).selectinload(Usuario.empresa),
            selectinload(EventoApp.dispositivo),
        )
        .where(EventoApp.ts >= range_from, EventoApp.ts <= range_to)
        .order_by(EventoApp.ts.asc())
        .limit(MAX_ROWS)
    )
    if empresa_scope is not None:
        stmt = stmt.where(
            EventoApp.usuarioId.in_(
                select(Usuario.id).where(Usuario.empresaId == empresa_scope)
            )
        )
    if usuarioId is not None:
        stmt = stmt.where(EventoApp.usuarioId == usuarioId)

    eventos = (await db.execute(stmt)).scalars().all()

    # Generator streaming — escribe header + filas a un buffer in-memory chico
    async def row_iter() -> AsyncIterator[bytes]:
        buf = io.StringIO()
        writer = csv.writer(buf, dialect="excel")
        writer.writerow(CSV_HEADERS)
        yield buf.getvalue().encode("utf-8")
        buf.seek(0); buf.truncate(0)

        for e in eventos:
            u = e.usuario
            d = e.dispositivo
            writer.writerow([
                e.ts.isoformat(),
                u.email if u else "",
                u.nombre if u else "",
                u.empresa.nombre if (u and u.empresa) else "",
                e.tipo.value,
                "" if e.inSchedule is None else ("true" if e.inSchedule else "false"),
                e.screenName or "",
                e.appPackage or "",
                d.deviceUuid if d else "",
            ])
            data = buf.getvalue()
            if data:
                yield data.encode("utf-8")
                buf.seek(0); buf.truncate(0)

    filename = (
        f"cognipilot-eventos-"
        f"{range_from.strftime('%Y%m%d')}-{range_to.strftime('%Y%m%d')}.csv"
    )
    return StreamingResponse(
        row_iter(),
        media_type="text/csv; charset=utf-8",
        headers={
            "content-disposition": f'attachment; filename="{filename}"',
            "x-row-count": str(len(eventos)),
        },
    )
