"""Endpoints de métricas para el admin UI.

Dos vistas:
  - GET /api/metrics/overview      : snapshot agregado (cards del dashboard)
  - GET /api/metrics/timeseries    : puntos {ts, value} para line charts

Ambos requieren admin_sistema (por ahora). En Fase C se puede agregar variantes
para supervisor (filtrado por empresa) y gerente (KPIs del negocio).

`/metrics` (Prometheus exposition) lo monta `prometheus-fastapi-instrumentator`
en main.py, sin auth (acceso solo desde la red interna del Docker).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import require_roles
from app.core.observability import (
    arq_jobs_total,
    events_ingested_total,
    fcm_push_total,
    get_app_uptime_seconds,
    queue_depth,
)
from app.models.usuario import Dispositivo
from app.schemas.metrics import (
    DeviceMetrics,
    EventMetrics,
    FcmMetrics,
    HttpMetrics,
    MetricsOverviewResponse,
    QueueMetrics,
    ServerInfo,
    TimeseriesPoint,
    TimeseriesResponse,
)
from app.services.prometheus_client import get_prometheus_client, now_ts

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

admin_only = require_roles("admin_sistema")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers para leer los counters in-process
# ─────────────────────────────────────────────────────────────────────────────


def _counter_total(counter) -> int:  # noqa: ANN001
    """Suma todos los samples de un Counter (suma sobre todas las labels)."""
    total = 0.0
    for metric in counter.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total"):
                total += sample.value
    return int(total)


def _counter_by_label(counter, label: str) -> dict[str, int]:  # noqa: ANN001
    out: dict[str, float] = {}
    for metric in counter.collect():
        for sample in metric.samples:
            if not sample.name.endswith("_total"):
                continue
            val = sample.labels.get(label)
            if val is None:
                continue
            out[val] = out.get(val, 0.0) + sample.value
    return {k: int(v) for k, v in out.items()}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/metrics/overview
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/overview", response_model=MetricsOverviewResponse, dependencies=[Depends(admin_only)])
async def overview(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MetricsOverviewResponse:
    settings = get_settings()

    # ── Server ────────────────────────────────────────────────────────────────
    server = ServerInfo(
        uptime_seconds=get_app_uptime_seconds(),
        version="0.1.0",
        env=settings.app_env,
    )

    # ── Events ──────────────────────────────────────────────────────────────
    events_total = _counter_total(events_ingested_total)
    events = EventMetrics(ingested_total=events_total)

    # ── FCM ──────────────────────────────────────────────────────────────────
    fcm_by_result = _counter_by_label(fcm_push_total, "result")
    fcm_success = fcm_by_result.get("success", 0)
    fcm_error = fcm_by_result.get("error", 0)
    fcm_sent = fcm_success + fcm_error
    fcm = FcmMetrics(
        sent_total=fcm_sent,
        success_total=fcm_success,
        error_total=fcm_error,
        success_rate=(fcm_success / fcm_sent) if fcm_sent > 0 else None,
    )

    # ── Devices (consulta a DB) ──────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    cutoff_5m = now - timedelta(minutes=5)
    cutoff_24h = now - timedelta(hours=24)

    total_devices_q = await db.execute(select(func.count(Dispositivo.id)))
    active_5m_q = await db.execute(
        select(func.count(Dispositivo.id)).where(Dispositivo.lastSeen >= cutoff_5m)
    )
    active_24h_q = await db.execute(
        select(func.count(Dispositivo.id)).where(Dispositivo.lastSeen >= cutoff_24h)
    )

    devices = DeviceMetrics(
        registered_total=int(total_devices_q.scalar() or 0),
        active_5m=int(active_5m_q.scalar() or 0),
        active_24h=int(active_24h_q.scalar() or 0),
    )

    # ── Queue (lo que tenemos in-process; mejorará con la integración Redis directa) ──
    completed = _counter_by_label(arq_jobs_total, "status").get("ok", 0)
    failed = _counter_by_label(arq_jobs_total, "status").get("fail", 0)
    # depth: leer del gauge actual (default 0 si no se actualizó nunca)
    depth_value = 0
    for metric in queue_depth.collect():
        for sample in metric.samples:
            depth_value = int(sample.value)
            break

    qm = QueueMetrics(
        depth=depth_value,
        jobs_completed_total=completed,
        jobs_failed_total=failed,
    )

    # ── HTTP (vía Prometheus si está, sino solo total in-process) ────────────
    prom = get_prometheus_client()
    prom_ok = True
    http_q = HttpMetrics(requests_total=0)

    rps_data = await prom.query(
        'sum(rate(http_requests_total{handler!="/metrics"}[5m]))'
    )
    if rps_data is None:
        prom_ok = False
    else:
        rps = _first_value(rps_data)
        http_q.requests_per_second_5m = rps

        err_data = await prom.query(
            'sum(rate(http_requests_total{status=~"5..", handler!="/metrics"}[5m])) / '
            'sum(rate(http_requests_total{handler!="/metrics"}[5m]))'
        )
        http_q.error_rate_5m = _first_value(err_data) if err_data else None

        for q_label, attr in (("0.50", "latency_p50_ms"), ("0.95", "latency_p95_ms"), ("0.99", "latency_p99_ms")):
            lat_data = await prom.query(
                f'histogram_quantile({q_label}, sum(rate(http_request_duration_seconds_bucket'
                f'{{handler!="/metrics"}}[5m])) by (le))'
            )
            val = _first_value(lat_data) if lat_data else None
            if val is not None:
                setattr(http_q, attr, val * 1000.0)

        tot_data = await prom.query('sum(http_requests_total{handler!="/metrics"})')
        tot = _first_value(tot_data) if tot_data else None
        if tot is not None:
            http_q.requests_total = int(tot)

    return MetricsOverviewResponse(
        server=server,
        http=http_q,
        events=events,
        devices=devices,
        fcm=fcm,
        queue=qm,
        prometheus_available=prom_ok,
    )


def _first_value(prom_data: dict) -> float | None:
    """Extrae el primer valor escalar de una respuesta de query instantánea."""
    result = prom_data.get("result") if isinstance(prom_data, dict) else None
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/metrics/timeseries
# ─────────────────────────────────────────────────────────────────────────────

_KNOWN_METRICS: dict[str, str] = {
    "requests_rate": 'sum(rate(http_requests_total{handler!="/metrics"}[1m]))',
    "error_rate": (
        'sum(rate(http_requests_total{status=~"5..", handler!="/metrics"}[5m])) / '
        'sum(rate(http_requests_total{handler!="/metrics"}[5m]))'
    ),
    "latency_p95_ms": (
        '1000 * histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket'
        '{handler!="/metrics"}[5m])) by (le))'
    ),
    "events_rate": "sum(rate(cognipilot_events_ingested_total[1m]))",
    "fcm_success_rate": (
        "sum(rate(cognipilot_fcm_push_total{result=\"success\"}[5m])) / "
        "sum(rate(cognipilot_fcm_push_total[5m]))"
    ),
    "queue_depth": "max(cognipilot_arq_queue_depth)",
}

_WINDOW_TO_SECONDS = {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}


@router.get("/timeseries", response_model=TimeseriesResponse, dependencies=[Depends(admin_only)])
async def timeseries(
    metric: Annotated[Literal[
        "requests_rate", "error_rate", "latency_p95_ms",
        "events_rate", "fcm_success_rate", "queue_depth",
    ], Query()],
    window: Annotated[Literal["15m", "1h", "6h", "24h", "7d"], Query()] = "1h",
    step: Annotated[int, Query(ge=15, le=3600)] = 60,
) -> TimeseriesResponse:
    expr = _KNOWN_METRICS[metric]
    window_seconds = _WINDOW_TO_SECONDS[window]
    end = now_ts()
    start = end - window_seconds

    prom = get_prometheus_client()
    raw = await prom.query_range(expr, start_ts=start, end_ts=end, step_seconds=step)

    points = [TimeseriesPoint(ts=ts, value=val) for ts, val in raw]
    return TimeseriesResponse(
        metric=metric,
        window=window,
        step=f"{step}s",
        points=points,
        prometheus_available=len(points) > 0,
    )
