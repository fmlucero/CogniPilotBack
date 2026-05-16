"""arq worker — Redis-backed async job queue.

Arrancarlo: `arq app.workers.tasks.WorkerSettings`

Tareas actuales:
  - send_schedule_push_task: enviar push FCM async (saca FCM del request path)

Diseño:
  - /api/events/bulk    → procesamiento inline en una transacción (suficiente para 30k usuarios)
  - /api/positions      → procesamiento inline con haversine (1 SELECT + 1 INSERT/UPDATE)
  - /api/schedule POST  → ENCOLA acá (1 sola en uso, evita bloquear request en Google FCM)

Si más adelante el load testing muestra que /events/bulk o /positions saturan,
podemos mover esos a tasks acá con bulk_insert_events_task / process_position_task.
"""
from __future__ import annotations

import logging
from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.services.fcm import send_schedule_push as _send_schedule_push

logger = logging.getLogger(__name__)

_settings = get_settings()


async def send_schedule_push_task(
    ctx: dict[str, Any],
    *,
    enabled: bool,
    time_from: str,
    time_to: str,
    tz: str,
) -> str:
    """Envía un push FCM al topic schedule-updates.

    Se ejecuta en el worker arq. Hasta 3 reintentos automáticos en caso de
    fallo transitorio (configurado en WorkerSettings.max_tries).
    """
    msg_id = _send_schedule_push(
        enabled=enabled, time_from=time_from, time_to=time_to, tz=tz
    )
    logger.info("FCM push sent (worker): %s", msg_id)
    return msg_id


class WorkerSettings:
    """arq Worker config."""

    functions = [send_schedule_push_task]

    redis_settings = RedisSettings.from_dsn(_settings.redis_url)

    # Retries automáticos en fallos transitorios
    max_tries = 3
    job_timeout = 30
    keep_result = 60
    health_check_interval = 30
