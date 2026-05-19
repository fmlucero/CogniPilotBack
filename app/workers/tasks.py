"""arq worker — Redis-backed async job queue.

Arrancarlo: `arq app.workers.tasks.WorkerSettings`

Post HU-17 NO hay tasks activas (se removió `send_schedule_push_task` cuando
se retiró Firebase Cloud Messaging del stack). El worker se mantiene corriendo
para que futuras tasks tengan donde ejecutarse:

  - Procesamiento batch de eventos en alta carga (si `/events/bulk` se vuelve
    insuficiente)
  - Re-encolado de jobs fallidos
  - Cron jobs (active devices, cleanup, etc.)

Para agregar una tarea: definir async function, agregarla a `functions=[...]`.
"""
from __future__ import annotations

from arq.connections import RedisSettings

from app.core.config import get_settings

_settings = get_settings()


class WorkerSettings:
    """arq Worker config — sin functions activas tras HU-17."""

    functions: list = []  # type: ignore[type-arg]

    redis_settings = RedisSettings.from_dsn(_settings.redis_url)

    max_tries = 3
    job_timeout = 30
    keep_result = 60
    health_check_interval = 30
