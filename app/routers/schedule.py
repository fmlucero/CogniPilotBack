"""Endpoints de schedule (ventana horaria) — port de cognipilot-remote/app/api/schedule/route.ts.

El push FCM se ENCOLA en arq y se envía desde el worker. Esto saca el
roundtrip a Google FCM (típicamente 100–300ms) del request path:
el supervisor recibe respuesta 200 inmediatamente y el push viaja async.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.arq_client import get_arq_pool
from app.core.db import get_session
from app.core.deps import CurrentUser, require_roles
from app.models.enums import AccionRegla, TipoRegla
from app.models.regla import Regla, ReglaHistorial
from app.schemas.schedule import (
    ScheduleResponse,
    ScheduleUpdateRequest,
    ScheduleUpdateResponse,
)

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
    # Resolver empresa: admin_sistema necesita ?empresaId=, supervisor usa la suya
    empresa_id = current["empresaId"]
    if current["rol"] == "admin_sistema" and empresaId:
        empresa_id = empresaId
    if not empresa_id:
        raise HTTPException(
            status_code=422,
            detail="empresaId required (admin must pass ?empresaId=)",
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

    # Encolar el push FCM al worker arq. Si Redis está caído, fallback a sync
    # para no perder la notificación (con el costo de bloquear unos ms el response).
    fcm_queued = False
    fcm_error: str | None = None
    fcm_message_id: str | None = None
    try:
        arq = get_arq_pool()
        await arq.enqueue_job(
            "send_schedule_push_task",
            enabled=body.enabled,
            time_from=body.time_from,
            time_to=body.time_to,
            tz=body.tz,
        )
        fcm_queued = True
    except Exception as e:  # noqa: BLE001
        logger.warning("arq enqueue failed, falling back to sync FCM: %s", e)
        from app.services.fcm import send_schedule_push
        try:
            fcm_message_id = send_schedule_push(
                enabled=body.enabled,
                time_from=body.time_from,
                time_to=body.time_to,
                tz=body.tz,
            )
        except Exception as e2:  # noqa: BLE001
            fcm_error = str(e2)

    return ScheduleUpdateResponse(
        enabled=regla.activa,
        time_from=regla.condicion.get("desde"),
        time_to=regla.condicion.get("hasta"),
        tz=regla.condicion.get("tz"),
        updatedAt=int(regla.updatedAt.timestamp() * 1000),
        updatedBy=current["email"],
        fcmQueued=fcm_queued,
        fcmMessageId=fcm_message_id,
        fcmError=fcm_error,
    )
