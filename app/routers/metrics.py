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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import CurrentUser, require_roles
from app.core.observability import (
    arq_jobs_total,
    events_ingested_total,
    get_app_uptime_seconds,
    queue_depth,
)
from app.models.eventos import EventoApp
from app.models.usuario import Dispositivo, Usuario
from app.models.empresa import Empresa
from app.schemas.metrics import (
    DeviceMetrics,
    EventMetrics,
    HealthResponse,
    HealthService,
    HttpMetrics,
    KpisByDay,
    KpisByType,
    KpisRange,
    KpisResponse,
    KpisTopUser,
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
    "queue_depth": "max(cognipilot_arq_queue_depth)",
}

_WINDOW_TO_SECONDS = {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}


@router.get("/timeseries", response_model=TimeseriesResponse, dependencies=[Depends(admin_only)])
async def timeseries(
    metric: Annotated[Literal[
        "requests_rate", "error_rate", "latency_p95_ms",
        "events_rate", "queue_depth",
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


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/metrics/kpis — HU-14: agregados sobre EventoApp para la home del gerente.
# Scope: admin global, supervisor/gerente su empresa (?empresaId=<otra> → 403),
# repartidor 403.
# ─────────────────────────────────────────────────────────────────────────────


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


@router.get("/kpis", response_model=KpisResponse)
async def kpis(
    current: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[int | None, Query(alias="from")] = None,
    to: Annotated[int | None, Query()] = None,
    empresaId: Annotated[str | None, Query()] = None,
    usuarioId: Annotated[str | None, Query()] = None,
) -> KpisResponse:
    rol = current["rol"]
    if rol == "repartidor":
        raise HTTPException(status_code=403, detail="Forbidden")

    # Rango default: últimos 7 días
    now = datetime.now(timezone.utc)
    range_to = _ms_to_dt(to) if to else now
    range_from = _ms_to_dt(from_) if from_ else (range_to - timedelta(days=7))
    if range_from > range_to:
        raise HTTPException(status_code=422, detail="`from` debe ser anterior a `to`")

    # Scope por empresa
    empresa_scope: str | None = None
    if rol == "admin_sistema":
        empresa_scope = empresaId
    else:  # supervisor o gerente
        if not current["empresaId"]:
            raise HTTPException(status_code=403, detail="Usuario sin empresa asignada")
        if empresaId and empresaId != current["empresaId"]:
            raise HTTPException(status_code=403, detail="Solo podés ver KPIs de tu propia empresa")
        empresa_scope = current["empresaId"]

    # Subquery del filtro común por usuario/empresa
    base_filters = [EventoApp.ts >= range_from, EventoApp.ts <= range_to]
    if empresa_scope is not None:
        base_filters.append(
            EventoApp.usuarioId.in_(
                select(Usuario.id).where(Usuario.empresaId == empresa_scope)
            )
        )
    if usuarioId is not None:
        base_filters.append(EventoApp.usuarioId == usuarioId)

    # 1) Total + active users
    totals_row = (
        await db.execute(
            select(
                func.count(EventoApp.id),
                func.count(func.distinct(EventoApp.usuarioId)),
            ).where(*base_filters)
        )
    ).one()
    events_total = int(totals_row[0] or 0)
    active_users = int(totals_row[1] or 0)

    # 2) Por día (date_trunc 'day' en UTC)
    by_day_rows = (
        await db.execute(
            select(
                func.date_trunc("day", EventoApp.ts).label("day"),
                func.count(EventoApp.id),
            )
            .where(*base_filters)
            .group_by("day")
            .order_by("day")
        )
    ).all()
    by_day = [
        KpisByDay(date=row[0].date().isoformat(), count=int(row[1]))
        for row in by_day_rows if row[0] is not None
    ]

    # 3) Por tipo
    by_type_rows = (
        await db.execute(
            select(EventoApp.tipo, func.count(EventoApp.id))
            .where(*base_filters)
            .group_by(EventoApp.tipo)
            .order_by(func.count(EventoApp.id).desc())
        )
    ).all()
    by_type = [KpisByType(tipo=row[0].value, count=int(row[1])) for row in by_type_rows]

    # 4) Top usuarios (limit 10)
    top_rows = (
        await db.execute(
            select(
                EventoApp.usuarioId,
                Usuario.nombre,
                Empresa.nombre,
                func.count(EventoApp.id),
            )
            .join(Usuario, EventoApp.usuarioId == Usuario.id, isouter=True)
            .join(Empresa, Usuario.empresaId == Empresa.id, isouter=True)
            .where(*base_filters)
            .group_by(EventoApp.usuarioId, Usuario.nombre, Empresa.nombre)
            .order_by(func.count(EventoApp.id).desc())
            .limit(10)
        )
    ).all()
    top_users = [
        KpisTopUser(
            usuarioId=row[0],
            usuarioNombre=row[1],
            empresaNombre=row[2],
            count=int(row[3]),
        )
        for row in top_rows
    ]

    return KpisResponse(
        range=KpisRange(
            start=int(range_from.timestamp() * 1000),
            end=int(range_to.timestamp() * 1000),
        ),
        events_total=events_total,
        active_users=active_users,
        by_day=by_day,
        by_type=by_type,
        top_users=top_users,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/metrics/health — HU-38: salud del sistema (containers + lag)
# Admin only. Hace checks contra cada servicio dependiente del back y
# devuelve un snapshot de estado + lag de eventos para detectar app-side
# silencios.
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse, dependencies=[Depends(admin_only)])
async def system_health(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HealthResponse:
    import time as _time
    import httpx as _httpx
    from app.core.arq_client import get_arq_pool
    from app.core.config import get_settings as _gs
    from app.models.eventos import EventoApp

    services: list[HealthService] = []

    # back-api: estamos respondiendo, ergo up
    services.append(HealthService(name="back-api", status="up", detail="self-check"))

    # postgres: SELECT 1
    pg_t0 = _time.perf_counter()
    try:
        from sqlalchemy import text as _text
        await db.execute(_text("SELECT 1"))
        services.append(HealthService(name="postgres", status="up", detail=f"{(_time.perf_counter() - pg_t0) * 1000:.0f}ms"))
    except Exception as e:  # noqa: BLE001
        services.append(HealthService(name="postgres", status="down", detail=str(e)[:120]))

    # redis: PING via el pool de arq (reuse de la conexión existente)
    try:
        pool = get_arq_pool()
        r_t0 = _time.perf_counter()
        ok = await pool.ping()  # type: ignore[attr-defined]
        services.append(HealthService(
            name="redis",
            status="up" if ok else "down",
            detail=f"{(_time.perf_counter() - r_t0) * 1000:.0f}ms",
        ))
    except Exception as e:  # noqa: BLE001
        services.append(HealthService(name="redis", status="down", detail=str(e)[:120]))

    # prometheus: GET prometheus:9090/-/healthy
    prom_url = (_gs().prometheus_url or "http://prometheus:9090").rstrip("/")
    try:
        async with _httpx.AsyncClient(timeout=3.0) as cli:
            p_t0 = _time.perf_counter()
            r = await cli.get(f"{prom_url}/-/healthy")
            services.append(HealthService(
                name="prometheus",
                status="up" if r.status_code == 200 else "down",
                detail=f"HTTP {r.status_code} · {(_time.perf_counter() - p_t0) * 1000:.0f}ms",
            ))
    except Exception as e:  # noqa: BLE001
        services.append(HealthService(name="prometheus", status="unknown", detail=str(e)[:120]))

    # eventos lag — cuánto hace del último evento ingestado
    last_ts_row = (
        await db.execute(select(func.max(EventoApp.ts)))
    ).scalar()
    eventos_lag_seconds: float | None = None
    if last_ts_row is not None:
        last = last_ts_row if last_ts_row.tzinfo else last_ts_row.replace(tzinfo=timezone.utc)
        eventos_lag_seconds = max(0.0, (datetime.now(timezone.utc) - last).total_seconds())

    # devices activos en 5min (snapshot, mismo cutoff que /overview)
    cutoff_5m = datetime.now(timezone.utc) - timedelta(minutes=5)
    devices_5m = (
        await db.execute(
            select(func.count(Dispositivo.id)).where(Dispositivo.lastSeen >= cutoff_5m)
        )
    ).scalar() or 0

    return HealthResponse(
        services=services,
        uptime_seconds=get_app_uptime_seconds(),
        eventos_lag_seconds=eventos_lag_seconds,
        devices_active_5m=int(devices_5m),
        checked_at=int(datetime.now(timezone.utc).timestamp() * 1000),
    )
