"""arq worker — Redis-backed async job queue.

Para arrancarlo: `arq app.workers.tasks.WorkerSettings`

Tareas (placeholder; se desarrollan en Fase B):
  - send_schedule_push: enviar push FCM async (saca a FCM del request path)
  - bulk_insert_events: drenar cola de eventos y bulk insert a Postgres
  - process_position: aplicar lógica "solo insertar si difiere >Xm"
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
    """Sends an FCM push notification to the schedule-updates topic.

    Runs in the arq worker. Retry up to 3 times on failure.
    """
    msg_id = _send_schedule_push(
        enabled=enabled, time_from=time_from, time_to=time_to, tz=tz
    )
    logger.info("FCM push sent (worker): %s", msg_id)
    return msg_id


async def bulk_insert_events_task(ctx: dict[str, Any], events: list[dict]) -> int:
    """Placeholder Fase B: drenar cola de eventos y bulk insert.

    Tomar la lista, validar, insertar con `bulk_insert_mappings` o `insert(...).values([...])`.
    """
    logger.info("bulk_insert_events_task received %d events (TODO Fase B)", len(events))
    return len(events)


async def process_position_task(
    ctx: dict[str, Any],
    *,
    device_uuid: str,
    lat: str,
    lng: str,
    ts_ms: int,
) -> bool:
    """Placeholder Fase B: aplicar lógica de 'solo insertar si difiere'.

    Lee última Posicion del dispositivo, compara con la nueva, decide si inserta
    en Posicion o solo actualiza Dispositivo.lastLat/Lng/lastSeen.
    """
    logger.info("process_position_task received: %s @ %s,%s (TODO Fase B)", device_uuid, lat, lng)
    return False


class WorkerSettings:
    """arq Worker config."""

    functions = [
        send_schedule_push_task,
        bulk_insert_events_task,
        process_position_task,
    ]

    redis_settings = RedisSettings.from_dsn(_settings.redis_url)

    # Retries y timeouts conservadores
    max_tries = 3
    job_timeout = 30
    keep_result = 60
    health_check_interval = 30
