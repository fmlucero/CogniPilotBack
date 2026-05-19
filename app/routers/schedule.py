"""Endpoints de schedule (ventana horaria) — port de cognipilot-remote/app/api/schedule/route.ts.

Post HU-18: el back ya NO envía push FCM al cambiar el horario. La app
del repartidor consulta este endpoint por polling (foreground cada 30s,
WorkManager cada 15 min) y detecta el cambio comparando contra su snapshot
local. Cuando exista, también se enviará via SSE en /api/realtime/stream.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_session
from app.core.deps import CurrentUser, require_roles
from app.models.enums import AccionRegla, TipoRegla
from app.models.regla import Regla, ReglaHistorial
from app.schemas.schedule import (
    ScheduleResponse,
    ScheduleUpdateRequest,
    ScheduleUpdateResponse,
)
from app.services.realtime import publish_schedule_updated

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

supervisor_or_admin = require_roles("supervisor", "admin_sistema")


def _regla_to_shape(regla: Regla | None, updated_by: str | None) -> ScheduleResponse:
    if regla is None:
        return ScheduleResponse(
            enabled=False, time_from=None, time_to=None, tz=None, updatedAt=None, updatedBy=None
        )
    cond = regla.condicion or {}
    return ScheduleResponse(
        enabled=regla.activa,
        time_from=cond.get("desde"),
        time_to=cond.get("hasta"),
        tz=cond.get("tz"),
        updatedAt=int(regla.updatedAt.timestamp() * 1000),
        updatedBy=updated_by,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/schedule — público (la app Android lo usa como fallback al push FCM)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=ScheduleResponse)
async def get_schedule(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ScheduleResponse:
    stmt = (
        select(Regla)
        .where(Regla.tipo == TipoRegla.ventana_horaria, Regla.rutaId.is_(None))
        .order_by(desc(Regla.updatedAt))
        .options(selectinload(Regla.historial).selectinload(ReglaHistorial.usuario))
        .limit(1)
    )
    regla = (await db.execute(stmt)).scalar_one_or_none()
    updated_by = None
    if regla and regla.historial:
        first_hist = sorted(regla.historial, key=lambda h: h.ts, reverse=True)[0]
        updated_by = first_hist.usuario.email if first_hist.usuario else None
    return _regla_to_shape(regla, updated_by)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/schedule — supervisor o admin
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", response_model=ScheduleUpdateResponse)
async def update_schedule(
    body: ScheduleUpdateRequest,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[Any, Depends(supervisor_or_admin)],
    empresaId: Annotated[str | None, Query()] = None,
) -> ScheduleUpdateResponse:
    # Resolver empresa:
    #  - supervisor: usa la suya (current["empresaId"])
    #  - admin_sistema: si pasa ?empresaId=, usa esa; si no, fallback a la empresa
    #    de la regla ventana_horaria más reciente (consistente con el GET, que
    #    muestra esa misma regla). Útil para el dashboard global del admin sin
    #    forzarlo a elegir empresa.
    empresa_id = current["empresaId"]
    if current["rol"] == "admin_sistema":
        if empresaId:
            empresa_id = empresaId
        else:
            most_recent_rule_empresa = (
                await db.execute(
                    select(Regla.empresaId)
                    .where(
                        Regla.tipo == TipoRegla.ventana_horaria,
                        Regla.rutaId.is_(None),
                    )
                    .order_by(desc(Regla.updatedAt))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if most_recent_rule_empresa:
                empresa_id = most_recent_rule_empresa
    if not empresa_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "No hay una regla de ventana horaria existente para usar como default. "
                "Como admin_sistema, pasá ?empresaId=<uuid> para crear la primera regla "
                "de una empresa específica."
            ),
        )

    # Buscar regla existente (única ventana_horaria global de la empresa)
    existing = (
        await db.execute(
            select(Regla).where(
                Regla.empresaId == empresa_id,
                Regla.tipo == TipoRegla.ventana_horaria,
                Regla.rutaId.is_(None),
            )
        )
    ).scalar_one_or_none()

    condicion = {"desde": body.time_from, "hasta": body.time_to, "tz": body.tz}

    if existing is not None:
        before = {"activa": existing.activa, "condicion": existing.condicion}
        existing.activa = body.enabled
        existing.condicion = condicion
        regla = existing
        db.add(
            ReglaHistorial(
                reglaId=regla.id,
                usuarioId=current["sub"],
                campo="(actualización)",
                valorOld=before,
                valorNew={"activa": body.enabled, "condicion": condicion},
            )
        )
    else:
        regla = Regla(
            empresaId=empresa_id,
            nombre="Ventana horaria",
            tipo=TipoRegla.ventana_horaria,
            accion=AccionRegla.bloquear,
            activa=body.enabled,
            condicion=condicion,
        )
        db.add(regla)
        await db.flush()
        db.add(
            ReglaHistorial(
                reglaId=regla.id,
                usuarioId=current["sub"],
                campo="(creación)",
                valorOld=None,
                valorNew={"activa": body.enabled, "condicion": condicion},
            )
        )

    await db.commit()
    await db.refresh(regla)

    payload = {
        "enabled": regla.activa,
        "from": regla.condicion.get("desde"),
        "to": regla.condicion.get("hasta"),
        "tz": regla.condicion.get("tz"),
        "updatedAt": int(regla.updatedAt.timestamp() * 1000),
        "updatedBy": current["email"],
    }

    # Broadcast SSE — apps suscritas a /api/realtime/stream reciben el cambio
    # en <100ms. Es best-effort: si Redis está caído, la app igual lo detecta
    # via polling de ScheduleSyncWorker (cada 15 min) o foreground (30s).
    await publish_schedule_updated(payload)

    return ScheduleUpdateResponse(**payload)
