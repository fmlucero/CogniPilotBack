"""Schemas Pydantic para los endpoints de métricas del admin UI."""
from __future__ import annotations

from pydantic import BaseModel


class ServerInfo(BaseModel):
    uptime_seconds: float
    version: str
    env: str


class HttpMetrics(BaseModel):
    requests_total: int
    requests_per_second_5m: float | None = None
    error_rate_5m: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None


class EventMetrics(BaseModel):
    ingested_total: int
    ingested_per_minute_1h: float | None = None


class DeviceMetrics(BaseModel):
    registered_total: int
    active_5m: int
    active_24h: int


class QueueMetrics(BaseModel):
    depth: int
    jobs_completed_total: int
    jobs_failed_total: int


class MetricsOverviewResponse(BaseModel):
    server: ServerInfo
    http: HttpMetrics
    events: EventMetrics
    devices: DeviceMetrics
    queue: QueueMetrics
    prometheus_available: bool


class TimeseriesPoint(BaseModel):
    ts: int       # epoch seconds
    value: float


class TimeseriesResponse(BaseModel):
    metric: str
    window: str
    step: str
    points: list[TimeseriesPoint]
    prometheus_available: bool


# ─── HU-14 — KPIs históricos para el gerente ─────────────────────────────────


class KpisRange(BaseModel):
    start: int  # ms epoch
    end: int


class KpisByDay(BaseModel):
    date: str          # ISO YYYY-MM-DD
    count: int


class KpisByType(BaseModel):
    tipo: str
    count: int


class KpisTopUser(BaseModel):
    usuarioId: str | None
    usuarioNombre: str | None
    empresaNombre: str | None
    count: int


class KpisResponse(BaseModel):
    range: KpisRange
    events_total: int
    active_users: int     # usuarios distintos con al menos un evento en el rango
    by_day: list[KpisByDay]
    by_type: list[KpisByType]
    top_users: list[KpisTopUser]


# ─── HU-38 — salud del sistema ───────────────────────────────────────────────


class HealthService(BaseModel):
    name: str
    status: str           # up | down | unknown
    detail: str | None = None     # mensaje breve del check (ej. "ping 12ms", "timeout")


class HealthResponse(BaseModel):
    services: list[HealthService]
    uptime_seconds: float
    eventos_lag_seconds: float | None   # delta entre now y max(EventoApp.ts); null si no hay eventos
    devices_active_5m: int
    checked_at: int       # ms epoch del chequeo
