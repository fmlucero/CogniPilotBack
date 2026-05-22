"""arq worker — Redis-backed async job queue.

Arrancarlo: `arq app.workers.tasks.WorkerSettings`

HU-12 reactivó el worker. Task registrada:
  - check_repartidor_threshold(ctx, repartidor_id, empresa_id) — cuenta
    scan_detected + user_continued del repartidor en la jornada local;
    si supera Empresa.umbralErroresJornada, crea una Alerta (si no hay
    una idéntica de hoy) y la publica al SSE channel realtime:alerta.

Si en el futuro hay otras tasks (cron jobs, batch processing), se agregan
a `functions` y se registran con sus parámetros estandarizados (ctx + args).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from arq.connections import RedisSettings
from sqlalchemy import and_, select

from app.core.audit import log_audit
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.alerta import Alerta
from app.models.empresa import Empresa
from app.models.enums import TipoEvento
from app.models.eventos import EventoApp
from app.models.usuario import Dispositivo, Usuario

logger = logging.getLogger("cognipilot.worker")

_settings = get_settings()

# Misma TZ que /api/me/ruta — la jornada operativa se mide en hora local de
# Argentina (no UTC). Si llamamos a las 23:00 ART, "hoy" sigue siendo el día
# operativo en curso aunque UTC ya cambió.
_FLEET_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# Tipos de evento que cuentan como "error bloqueado" para el umbral.
ERROR_EVENT_TYPES = (TipoEvento.scan_detected, TipoEvento.user_continued)

# Canal Redis pub/sub donde publicamos las alertas para que /api/realtime/stream
# las emita en vivo a los supervisores conectados.
CHANNEL_ALERTA = "realtime:alerta"


def _today_local_range_utc() -> tuple[datetime, datetime]:
    """Devuelve [inicio, fin) del día operativo local convertido a UTC."""
    now_local = datetime.now(_FLEET_TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def check_repartidor_threshold(
    ctx: dict[str, Any],
    repartidor_id: str,
    empresa_id: str | None = None,
) -> dict[str, Any]:
    """HU-12 — Evalúa si el repartidor superó el umbral de errores hoy.

    Idempotente: si ya hay una alerta `umbral_errores` para este repartidor en
    la jornada local, no se vuelve a emitir (ahorra spam si el repartidor
    sigue acumulando). Se reactivará al día siguiente cuando arranca una
    nueva jornada.

    Devuelve dict con 'created' (bool) y datos auxiliares para debug/logs.
    """
    day_start_utc, day_end_utc = _today_local_range_utc()

    async with SessionLocal() as db:
        # Resolver empresa y umbral.
        user = (
            await db.execute(select(Usuario).where(Usuario.id == repartidor_id))
        ).scalar_one_or_none()
        if user is None or not user.activo:
            return {"created": False, "reason": "user_not_found_or_inactive"}
        emp_id = empresa_id or user.empresaId
        if emp_id is None:
            return {"created": False, "reason": "user_without_empresa"}

        empresa = (
            await db.execute(select(Empresa).where(Empresa.id == emp_id))
        ).scalar_one_or_none()
        if empresa is None:
            return {"created": False, "reason": "empresa_not_found"}
        umbral = empresa.umbralErroresJornada

        # Contar eventos error de hoy.
        count_q = await db.execute(
            select(EventoApp.id).where(
                and_(
                    EventoApp.usuarioId == repartidor_id,
                    EventoApp.tipo.in_(ERROR_EVENT_TYPES),
                    EventoApp.ts >= day_start_utc,
                    EventoApp.ts < day_end_utc,
                )
            )
        )
        errores_hoy = len(count_q.all())

        if errores_hoy < umbral:
            return {"created": False, "errores_hoy": errores_hoy, "umbral": umbral}

        # ¿Ya hay alerta de hoy para este repartidor?
        existing = (
            await db.execute(
                select(Alerta).where(
                    and_(
                        Alerta.repartidorId == repartidor_id,
                        Alerta.tipo == "umbral_errores",
                        Alerta.ts >= day_start_utc,
                        Alerta.ts < day_end_utc,
                    )
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {
                "created": False,
                "reason": "already_emitted_today",
                "alerta_id": existing.id,
                "errores_hoy": errores_hoy,
            }

        # Última posición conocida (criterio 4 del card).
        last_pos = (
            await db.execute(
                select(Dispositivo)
                .where(
                    and_(
                        Dispositivo.usuarioId == repartidor_id,
                        Dispositivo.lastLat.is_not(None),
                        Dispositivo.lastLng.is_not(None),
                    )
                )
                .order_by(Dispositivo.lastSeen.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        payload = {
            "repartidor_id": user.id,
            "repartidor_nombre": user.nombre,
            "repartidor_email": user.email,
            "errores_hoy": errores_hoy,
            "umbral": umbral,
            "lat": float(last_pos.lastLat) if last_pos and last_pos.lastLat is not None else None,
            "lng": float(last_pos.lastLng) if last_pos and last_pos.lastLng is not None else None,
            "last_seen": (
                int(last_pos.lastSeen.replace(tzinfo=timezone.utc).timestamp() * 1000)
                if last_pos and last_pos.lastSeen and last_pos.lastSeen.tzinfo is None
                else int(last_pos.lastSeen.timestamp() * 1000)
                if last_pos and last_pos.lastSeen
                else None
            ),
        }

        alerta = Alerta(
            empresaId=emp_id,
            repartidorId=repartidor_id,
            tipo="umbral_errores",
            payload=payload,
            leida=False,
        )
        db.add(alerta)
        await db.commit()
        await db.refresh(alerta)

        log_audit(
            "alerta_emitida",
            usuario_id=repartidor_id,
            email=user.email,
            tipo="umbral_errores",
            alerta_id=alerta.id,
            errores_hoy=errores_hoy,
            umbral=umbral,
        )

        # Publish SSE channel (best-effort) — via services.realtime para que
        # el formato del payload quede en un solo lugar.
        from app.services.realtime import publish_alerta

        ts_aware = alerta.ts if alerta.ts.tzinfo else alerta.ts.replace(tzinfo=timezone.utc)
        sse_payload = {
            "type": "alerta",
            "alerta_id": alerta.id,
            "tipo": "umbral_errores",
            "empresa_id": emp_id,
            "ts": int(ts_aware.timestamp() * 1000),
            **payload,
        }
        await publish_alerta(sse_payload)

        return {
            "created": True,
            "alerta_id": alerta.id,
            "errores_hoy": errores_hoy,
            "umbral": umbral,
        }


class WorkerSettings:
    """arq Worker config — registra check_repartidor_threshold para HU-12."""

    functions = [check_repartidor_threshold]

    redis_settings = RedisSettings.from_dsn(_settings.redis_url)

    max_tries = 3
    job_timeout = 30
    keep_result = 60
    health_check_interval = 30
