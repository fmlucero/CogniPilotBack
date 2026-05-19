"""Observabilidad — Prometheus instrumentation + custom business metrics.

Dos capas:
  1. HTTP automático (vía prometheus-fastapi-instrumentator):
       - http_requests_total (counter, por method/handler/status)
       - http_request_duration_seconds (histogram, p50/p95/p99)
       - http_requests_in_progress (gauge)
  2. Negocio (definido manualmente acá):
       - events_ingested_total
       - active_devices (gauge actualizado con periodicidad)
       - arq_queue_depth, arq_jobs_total

Endpoint `/metrics` queda expuesto (sin auth) sobre la red interna del Docker.
nginx bloquea acceso externo a esa ruta.

Nota HU-18: las métricas FCM (fcm_push_total, fcm_push_duration_seconds) se
removieron al retirar Firebase del stack. Si se implementa SSE, agregar acá
contadores de conexiones SSE activas y mensajes broadcasted.
"""
from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

# ─────────────────────────────────────────────────────────────────────────────
# Business metrics — usados desde el resto de la app
# ─────────────────────────────────────────────────────────────────────────────

events_ingested_total = Counter(
    "cognipilot_events_ingested_total",
    "Eventos ingresados por la app móvil",
    labelnames=("tipo",),
)

# Post HU-18 ya no hay métricas FCM (se removió el sistema de push externo).
# Cuando se implemente SSE, agregar contadores de conexiones SSE activas y
# mensajes broadcasted aquí.

active_devices = Gauge(
    "cognipilot_active_devices",
    "Dispositivos con lastSeen reciente",
    labelnames=("window",),  # 5m | 24h
)

queue_depth = Gauge(
    "cognipilot_arq_queue_depth",
    "Jobs encolados en arq esperando worker",
    labelnames=("queue",),
)

arq_jobs_total = Counter(
    "cognipilot_arq_jobs_total",
    "Jobs ejecutados por arq",
    labelnames=("status", "task"),  # status ∈ {ok, retry, fail}
)


# ─────────────────────────────────────────────────────────────────────────────
# App start time — para uptime
# ─────────────────────────────────────────────────────────────────────────────

_app_start_time = time.time()


def get_app_uptime_seconds() -> float:
    return time.time() - _app_start_time


# ─────────────────────────────────────────────────────────────────────────────
# Instrumentator factory — llamado desde main.py
# ─────────────────────────────────────────────────────────────────────────────


def make_instrumentator() -> Instrumentator:
    """Configura prometheus-fastapi-instrumentator.

    Excluye `/metrics` y `/health*` del scraping para no contaminar las métricas
    con los hits del propio Prometheus y del healthcheck de Docker.
    """
    instr = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=False,
        excluded_handlers=["/metrics", "/health", "/health/db"],
    )

    # Métricas HTTP estándar
    from prometheus_fastapi_instrumentator import metrics

    instr.add(metrics.default())
    instr.add(metrics.requests())
    instr.add(metrics.latency(buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)))
    instr.add(metrics.request_size())
    instr.add(metrics.response_size())

    return instr
