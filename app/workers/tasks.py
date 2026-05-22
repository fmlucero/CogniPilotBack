"""arq worker — Redis-backed async job queue.

Arrancarlo: `arq app.workers.tasks.WorkerSettings`

Tasks registradas:
  - check_repartidor_threshold(ctx, repartidor_id, empresa_id) — disparado
    desde events.py tras cada ingesta de scan_detected/user_continued.
    Evalúa DOS criterios independientes:
      a) HU-12 umbral fijo: errores_hoy >= Empresa.umbralErroresJornada
         → alerta `umbral_errores`.
      b) HU-15 anomalía estadística: errores_hoy > mean + 2*stddev de las
         jornadas históricas del MISMO repartidor (mínimo 5 jornadas).
         → alerta `anomalia_estadistica`.
    Cada tipo es idempotente por día — no se duplica.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from arq.connections import RedisSettings
from sqlalchemy import and_, func, select

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


# HU-15 — ventana histórica para el cálculo. 90 días alcanza para tener una
# media estable y no incluir comportamiento muy antiguo (el repartidor pudo
# haber mejorado o cambiado de zona). Se podría enganchar al setting
# `retencion.eventos_dias` cuando exista el read del catálogo desde el worker.
_ANOMALY_HISTORY_DAYS = 90
_ANOMALY_MIN_JORNADAS = 5
_ANOMALY_SIGMAS = 2.0


async def _compute_anomaly(
    db, repartidor_id: str, day_start_utc: datetime
) -> dict[str, Any] | None:
    """HU-15 — Calcula si los errores de HOY del repartidor son anómalos
    respecto a su historia personal.

    Devuelve un dict con datos del cálculo si supera el umbral estadístico,
    `None` si no hay suficiente data o si está dentro de lo normal.

    Implementación: agrupa eventos error por día local (Argentina) usando
    Postgres `date_trunc('day', ts AT TIME ZONE 'America/Argentina/...')`,
    excluye hoy del cálculo de baseline. Si hay ≥5 jornadas históricas
    con datos, computa mean+stddev y compara contra hoy.
    """
    history_start = day_start_utc - timedelta(days=_ANOMALY_HISTORY_DAYS)

    # Agrupar por día LOCAL Argentina (ts at time zone Buenos_Aires → date).
    bucket = func.date(func.timezone("America/Argentina/Buenos_Aires", EventoApp.ts)).label("d")
    rows = (
        await db.execute(
            select(bucket, func.count(EventoApp.id))
            .where(
                and_(
                    EventoApp.usuarioId == repartidor_id,
                    EventoApp.tipo.in_(ERROR_EVENT_TYPES),
                    EventoApp.ts >= history_start,
                    EventoApp.ts < day_start_utc,  # excluye hoy
                )
            )
            .group_by(bucket)
        )
    ).all()

    counts_por_dia = [int(r[1]) for r in rows if r[1] is not None]
    jornadas = len(counts_por_dia)
    if jornadas < _ANOMALY_MIN_JORNADAS:
        return None

    mean = sum(counts_por_dia) / jornadas
    var = sum((c - mean) ** 2 for c in counts_por_dia) / jornadas
    stddev = math.sqrt(var)

    # Si stddev == 0 (todos los días iguales) requerimos al menos 1 desvío
    # arbitrario (=1.0) para no marcar anomalía por cualquier +1 sobre la media.
    effective_stddev = stddev if stddev > 0 else 1.0
    threshold = mean + _ANOMALY_SIGMAS * effective_stddev

    # Count de hoy.
    day_end_utc = day_start_utc + timedelta(days=1)
    today_count = (await db.execute(
        select(func.count(EventoApp.id)).where(
            and_(
                EventoApp.usuarioId == repartidor_id,
                EventoApp.tipo.in_(ERROR_EVENT_TYPES),
                EventoApp.ts >= day_start_utc,
                EventoApp.ts < day_end_utc,
            )
        )
    )).scalar_one()

    if today_count <= threshold:
        return None

    return {
        "errores_hoy": int(today_count),
        "mean": round(mean, 2),
        "stddev": round(stddev, 2),
        "threshold": round(threshold, 2),
        "jornadas_consideradas": jornadas,
        "ventana_dias": _ANOMALY_HISTORY_DAYS,
        "sigmas": _ANOMALY_SIGMAS,
    }


async def _create_and_publish_alerta(
    db,
    *,
    empresa_id: str,
    repartidor_id: str,
    tipo: str,
    payload: dict[str, Any],
) -> Alerta:
    """Inserta la fila Alerta, hace audit + publish SSE. La idempotencia
    (no duplicar por día) es responsabilidad del caller."""
    alerta = Alerta(
        empresaId=empresa_id,
        repartidorId=repartidor_id,
        tipo=tipo,
        payload=payload,
        leida=False,
    )
    db.add(alerta)
    await db.commit()
    await db.refresh(alerta)

    log_audit(
        "alerta_emitida",
        usuario_id=repartidor_id,
        email=payload.get("repartidor_email"),
        tipo=tipo,
        alerta_id=alerta.id,
    )

    from app.services.realtime import publish_alerta

    ts_aware = alerta.ts if alerta.ts.tzinfo else alerta.ts.replace(tzinfo=timezone.utc)
    sse_payload = {
        "type": "alerta",
        "alerta_id": alerta.id,
        "tipo": tipo,
        "empresa_id": empresa_id,
        "ts": int(ts_aware.timestamp() * 1000),
        **payload,
    }
    await publish_alerta(sse_payload)
    return alerta


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

        # Última posición conocida — comparte payload entre ambos tipos de alerta.
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

        def _last_seen_ms() -> int | None:
            if not last_pos or not last_pos.lastSeen:
                return None
            ls = last_pos.lastSeen
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
            return int(ls.timestamp() * 1000)

        common_payload: dict[str, Any] = {
            "repartidor_id": user.id,
            "repartidor_nombre": user.nombre,
            "repartidor_email": user.email,
            "lat": float(last_pos.lastLat) if last_pos and last_pos.lastLat is not None else None,
            "lng": float(last_pos.lastLng) if last_pos and last_pos.lastLng is not None else None,
            "last_seen": _last_seen_ms(),
        }

        result: dict[str, Any] = {"created_alerts": []}

        # ─── HU-12: umbral fijo ─────────────────────────────────────────────
        if errores_hoy >= umbral:
            existing_umbral = (
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
            if existing_umbral is None:
                payload = {**common_payload, "errores_hoy": errores_hoy, "umbral": umbral}
                alerta = await _create_and_publish_alerta(
                    db,
                    empresa_id=emp_id,
                    repartidor_id=repartidor_id,
                    tipo="umbral_errores",
                    payload=payload,
                )
                result["created_alerts"].append({"tipo": "umbral_errores", "alerta_id": alerta.id})

        # ─── HU-15: anomalía estadística ────────────────────────────────────
        # Independiente del umbral fijo: un repartidor que normalmente tiene
        # 1 error/día y hoy tiene 3 dispara anomalía aunque no llegue al
        # umbral global de 3.
        anomaly = await _compute_anomaly(db, repartidor_id, day_start_utc)
        if anomaly is not None:
            existing_anom = (
                await db.execute(
                    select(Alerta).where(
                        and_(
                            Alerta.repartidorId == repartidor_id,
                            Alerta.tipo == "anomalia_estadistica",
                            Alerta.ts >= day_start_utc,
                            Alerta.ts < day_end_utc,
                        )
                    )
                )
            ).scalar_one_or_none()
            if existing_anom is None:
                payload = {**common_payload, **anomaly}
                alerta = await _create_and_publish_alerta(
                    db,
                    empresa_id=emp_id,
                    repartidor_id=repartidor_id,
                    tipo="anomalia_estadistica",
                    payload=payload,
                )
                result["created_alerts"].append({"tipo": "anomalia_estadistica", "alerta_id": alerta.id})

        result["errores_hoy"] = errores_hoy
        result["umbral"] = umbral
        result["anomaly"] = anomaly
        result["created"] = bool(result["created_alerts"])
        return result


class WorkerSettings:
    """arq Worker config — registra check_repartidor_threshold para HU-12."""

    functions = [check_repartidor_threshold]

    redis_settings = RedisSettings.from_dsn(_settings.redis_url)

    max_tries = 3
    job_timeout = 30
    keep_result = 60
    health_check_interval = 30
