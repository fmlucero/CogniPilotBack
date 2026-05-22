"""HU-12 — Endpoints de alertas.

GET   /api/alertas         admin global, supervisor/gerente su empresa.
PATCH /api/alertas/{id}/leer  marca una alerta como leída.
PATCH /api/alertas/leer-todas marca todas las del scope como leídas.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
from app.core.db import get_session
from app.core.deps import CurrentUser
from app.models.alerta import Alerta
from app.models.empresa import Empresa
from app.models.usuario import Usuario
from app.schemas.alerta import AlertaResponse, AlertasListResponse

router = APIRouter(prefix="/api/alertas", tags=["alertas"])


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ms(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return int(_aware_utc(dt).timestamp() * 1000)


def _scope_empresa(current: dict) -> str | None:
    rol = current["rol"]
    if rol == "admin_sistema":
        return None  # sin filtro
    if rol in ("supervisor", "gerente"):
        if not current.get("empresaId"):
            raise HTTPException(status_code=403, detail="Usuario sin empresa asignada")
        return current["empresaId"]
    raise HTTPException(status_code=403, detail="Forbidden")


@router.get("", response_model=AlertasListResponse)
async def list_alertas(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    soloNoLeidas: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AlertasListResponse:
    scope = _scope_empresa(current)

    stmt = select(Alerta).order_by(Alerta.ts.desc()).limit(limit)
    count_stmt = select(func.count(Alerta.id)).where(Alerta.leida.is_(False))
    if scope is not None:
        stmt = stmt.where(Alerta.empresaId == scope)
        count_stmt = count_stmt.where(Alerta.empresaId == scope)
    if soloNoLeidas:
        stmt = stmt.where(Alerta.leida.is_(False))

    alertas = (await db.execute(stmt)).scalars().all()
    unread = int((await db.execute(count_stmt)).scalar() or 0)

    # Pre-cargar nombres de empresas + repartidores en una sola pasada cada uno.
    emp_ids = {a.empresaId for a in alertas}
    rep_ids = {a.repartidorId for a in alertas if a.repartidorId}
    emp_map: dict[str, str] = {}
    if emp_ids:
        rows = (await db.execute(select(Empresa.id, Empresa.nombre).where(Empresa.id.in_(emp_ids)))).all()
        emp_map = {r.id: r.nombre for r in rows}
    rep_map: dict[str, tuple[str, str]] = {}
    if rep_ids:
        rows = (await db.execute(select(Usuario.id, Usuario.nombre, Usuario.email).where(Usuario.id.in_(rep_ids)))).all()
        rep_map = {r.id: (r.nombre, r.email) for r in rows}

    return AlertasListResponse(
        alertas=[
            AlertaResponse(
                id=a.id,
                ts=_ms(a.ts) or 0,
                empresaId=a.empresaId,
                empresaNombre=emp_map.get(a.empresaId),
                repartidorId=a.repartidorId,
                repartidorNombre=rep_map.get(a.repartidorId, (None, None))[0] if a.repartidorId else None,
                repartidorEmail=rep_map.get(a.repartidorId, (None, None))[1] if a.repartidorId else None,
                tipo=a.tipo,
                payload=a.payload,
                leida=a.leida,
                leidaPor=a.leidaPor,
                leidaAt=_ms(a.leidaAt),
            )
            for a in alertas
        ],
        unreadCount=unread,
    )


@router.patch("/{alerta_id}/leer", response_model=AlertaResponse)
async def marcar_leida(
    alerta_id: str,
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AlertaResponse:
    alerta = (
        await db.execute(select(Alerta).where(Alerta.id == alerta_id))
    ).scalar_one_or_none()
    if alerta is None:
        raise HTTPException(status_code=404, detail="Alerta no existe")

    scope = _scope_empresa(current)
    if scope is not None and alerta.empresaId != scope:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not alerta.leida:
        alerta.leida = True
        alerta.leidaPor = current["sub"]
        alerta.leidaAt = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(alerta)
        log_audit(
            "alerta_leida",
            usuario_id=current["sub"],
            email=current.get("email"),
            alerta_id=alerta.id,
            tipo=alerta.tipo,
        )

    return AlertaResponse(
        id=alerta.id,
        ts=_ms(alerta.ts) or 0,
        empresaId=alerta.empresaId,
        empresaNombre=None,
        repartidorId=alerta.repartidorId,
        repartidorNombre=None,
        repartidorEmail=None,
        tipo=alerta.tipo,
        payload=alerta.payload,
        leida=alerta.leida,
        leidaPor=alerta.leidaPor,
        leidaAt=_ms(alerta.leidaAt),
    )


@router.patch("/leer-todas")
async def marcar_todas_leidas(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    scope = _scope_empresa(current)
    stmt = (
        update(Alerta)
        .where(and_(Alerta.leida.is_(False)))
        .values(leida=True, leidaPor=current["sub"], leidaAt=datetime.now(timezone.utc))
    )
    if scope is not None:
        stmt = stmt.where(Alerta.empresaId == scope)
    result = await db.execute(stmt)
    await db.commit()
    n = result.rowcount or 0
    log_audit(
        "alertas_leer_todas",
        usuario_id=current["sub"],
        email=current.get("email"),
        count=n,
    )
    return {"updated": n}
