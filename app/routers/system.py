"""HU-45..HU-49 — Endpoints de inspección de infraestructura.

Todos admin_sistema only (salvo HU-49 version, público). Se agrupan acá para
que la sección /sistema del front tenga un único namespace `/api/system/*`.

Recursos cubiertos:
  - GET /api/system/containers   HU-45: inventario completo via docker socket
  - GET /api/system/topology     HU-46: nodes + edges para el grafo
  - GET /api/system/requests     HU-47: últimas N peticiones HTTP (Redis ring)
  - GET /api/system/worker       HU-48: estado del worker arq
  - GET /api/system/version      HU-49: build/git/runtime info (este es público)
"""
from __future__ import annotations

import logging
import os
import platform
import re
import sys
from datetime import datetime, timezone
from typing import Any

import aiodocker
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.db import SessionLocal

from app.core.deps import CurrentUser
from app.services import http_recent
from app.services.realtime import _get_redis

router = APIRouter(prefix="/api/system", tags=["system"])
logger = logging.getLogger(__name__)


def _require_admin(current: dict) -> None:
    if current["rol"] != "admin_sistema":
        raise HTTPException(status_code=403, detail="Forbidden")


def _parse_started_at(s: str | None) -> int | None:
    """Docker devuelve un ISO como '2026-05-22T12:00:00.123456789Z'. Lo
    convertimos a ms epoch para que el front muestre 'hace X min'."""
    if not s:
        return None
    try:
        # Truncar nanosegundos a microsegundos y normalizar la 'Z'.
        s = s.replace("Z", "+00:00")
        if "." in s:
            head, _, frac = s.partition(".")
            offset_idx = frac.find("+")
            if offset_idx == -1:
                offset_idx = frac.find("-")
            if offset_idx == -1:
                micro = frac[:6]
                offset = ""
            else:
                micro = frac[:offset_idx][:6]
                offset = frac[offset_idx:]
            s = f"{head}.{micro}{offset}"
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _simplify_ports(ports: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Docker.list() devuelve ports con campos {IP, PrivatePort, PublicPort, Type}.
    Lo aplanamos a algo más fácil de consumir en el front."""
    if not ports:
        return []
    out = []
    for p in ports:
        out.append({
            "host_ip": p.get("IP"),
            "host_port": p.get("PublicPort"),
            "container_port": p.get("PrivatePort"),
            "type": p.get("Type", "tcp"),
        })
    return out


def _network_summary(net_settings: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Resumir Networks (dict por nombre) a lista plana con IP."""
    if not net_settings:
        return []
    nets = net_settings.get("Networks") or {}
    out = []
    for name, info in nets.items():
        out.append({
            "name": name,
            "ip": info.get("IPAddress") or None,
            "aliases": info.get("Aliases") or [],
        })
    return out


@router.get("/containers")
async def list_containers(current: CurrentUser) -> dict[str, Any]:
    """HU-45 — Inventario completo del daemon Docker."""
    _require_admin(current)

    docker = aiodocker.Docker()
    try:
        # all=True incluye exited; lo queremos para diagnosticar containers caídos.
        raw = await docker.containers.list(all=True)
        out: list[dict[str, Any]] = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        for c in raw:
            # aiodocker no popula todo el detalle en list(); necesitamos show()
            # para obtener State + RestartCount + Networks completas.
            try:
                detail = await c.show()
            except Exception:  # noqa: BLE001
                detail = c._container  # fallback al payload de list()

            state = detail.get("State") or {}
            name = (detail.get("Name") or "").lstrip("/")
            started_at_ms = _parse_started_at(state.get("StartedAt"))
            uptime_ms = (now_ms - started_at_ms) if started_at_ms else None

            health = (state.get("Health") or {}).get("Status")  # 'healthy'|'unhealthy'|'starting'|None

            out.append({
                "id": (detail.get("Id") or "")[:12],
                "name": name,
                "image": detail.get("Config", {}).get("Image") or detail.get("Image"),
                "state": state.get("Status"),       # running|exited|restarting|paused|...
                "running": bool(state.get("Running")),
                "started_at": started_at_ms,
                "uptime_ms": uptime_ms,
                "restart_count": detail.get("RestartCount") or 0,
                "health": health,
                "exit_code": state.get("ExitCode"),
                "error": (state.get("Error") or None) if not state.get("Running") else None,
                "ports": _simplify_ports(detail.get("NetworkSettings", {}).get("Ports") and [
                    {
                        "IP": (binding[0].get("HostIp") if binding else None),
                        "PublicPort": int(binding[0]["HostPort"]) if binding and binding[0].get("HostPort") else None,
                        "PrivatePort": int(port.split("/")[0]),
                        "Type": port.split("/")[1] if "/" in port else "tcp",
                    }
                    for port, binding in (detail.get("NetworkSettings", {}).get("Ports") or {}).items()
                ]),
                "networks": _network_summary(detail.get("NetworkSettings")),
                "command": detail.get("Config", {}).get("Cmd") or detail.get("Config", {}).get("Entrypoint"),
                "labels": {
                    # Solo labels del compose project — el resto es ruido (env, base images).
                    k: v for k, v in (detail.get("Config", {}).get("Labels") or {}).items()
                    if k.startswith("com.docker.compose")
                },
            })
        # Orden estable: running primero, después por nombre.
        out.sort(key=lambda c: (not c["running"], c["name"] or ""))

        return {
            "containers": out,
            "total": len(out),
            "running": sum(1 for c in out if c["running"]),
            "server_time": now_ms,
        }
    finally:
        await docker.close()


# ─────────────────────────────────────────────────────────────────────────────
# HU-46 — Topología del stack
# ─────────────────────────────────────────────────────────────────────────────
#
# El grafo es una representación del sistema productivo. Las cajas (nodes) y
# las flechas (edges) son fijas — describen *qué debería haber* — y el estado
# vivo (running, ip, ports, health) viene del daemon Docker. Si un container
# del registry no está vivo aparece con status="missing"; si aparece un
# container vivo fuera del registry, se devuelve con type="extra".
#
# Layout en columnas (para que el front pinte SVG sin libs):
#   col 0: external   (cloudflare tunnel)
#   col 1: proxy      (nginx)
#   col 2: app        (back-api, back-worker, app Next.js)
#   col 3: data       (postgres, redis)
#   col 4: observ     (prometheus)


_NODE_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "cloudflare",
        "label": "Cloudflare Tunnel",
        "type": "external",
        "column": 0,
        "container": None,  # systemd service en el host — no es container
        "default_ports": [{"port": 443, "type": "tcp", "label": "HTTPS"}],
        "note": "Quick tunnel sin cuenta — URL cambia con cada arranque del service",
    },
    {
        "id": "nginx",
        "label": "nginx",
        "type": "proxy",
        "column": 1,
        "container": "cognipilot-nginx",
        "default_ports": [{"port": 80, "type": "tcp", "label": "HTTP"}],
        "note": "Reverse proxy con resolver dinámico (Docker DNS)",
    },
    {
        "id": "app",
        "label": "Next.js (app)",
        "type": "app",
        "column": 2,
        "container": "cognipilot-app",
        "default_ports": [{"port": 3000, "type": "tcp", "label": "HTTP"}],
        "note": "Front Next.js post-cutover — solo UI, Server Components hacen fetch al back",
    },
    {
        "id": "back-api",
        "label": "FastAPI (back-api)",
        "type": "app",
        "column": 2,
        "container": "cognipilot-back-api",
        "default_ports": [{"port": 8000, "type": "tcp", "label": "HTTP"}],
        "note": "API principal — 18 routers + SSE + /metrics",
    },
    {
        "id": "back-worker",
        "label": "arq worker",
        "type": "app",
        "column": 2,
        "container": "cognipilot-back-worker",
        "default_ports": [],
        "note": "Tareas async (alerta umbral errores HU-12)",
    },
    {
        "id": "postgres",
        "label": "Postgres 16",
        "type": "data",
        "column": 3,
        "container": "cognipilot-postgres",
        "default_ports": [{"port": 5432, "type": "tcp", "label": "Postgres"}],
        "note": "DB principal — Alembic owns el schema",
    },
    {
        "id": "redis",
        "label": "Redis 7",
        "type": "data",
        "column": 3,
        "container": "cognipilot-redis",
        "default_ports": [{"port": 6379, "type": "tcp", "label": "Redis"}],
        "note": "Cache + pub/sub realtime + queue arq",
    },
    {
        "id": "prometheus",
        "label": "Prometheus",
        "type": "observ",
        "column": 4,
        "container": "cognipilot-prometheus",
        "default_ports": [{"port": 9090, "type": "tcp", "label": "HTTP"}],
        "note": "Scrape de /metrics cada 15s",
    },
]


_EDGE_REGISTRY: list[dict[str, Any]] = [
    {"from": "cloudflare", "to": "nginx", "label": "HTTPS → :80", "protocol": "http"},
    {"from": "nginx", "to": "back-api", "label": "/api/* :8000", "protocol": "http"},
    {"from": "nginx", "to": "back-api", "label": "/api/realtime/* (SSE)", "protocol": "sse"},
    {"from": "nginx", "to": "app", "label": "/* :3000", "protocol": "http"},
    {"from": "back-api", "to": "postgres", "label": "asyncpg :5432", "protocol": "tcp"},
    {"from": "back-api", "to": "redis", "label": "cache+pubsub :6379", "protocol": "tcp"},
    {"from": "back-worker", "to": "postgres", "label": "asyncpg :5432", "protocol": "tcp"},
    {"from": "back-worker", "to": "redis", "label": "arq queue :6379", "protocol": "tcp"},
    {"from": "prometheus", "to": "back-api", "label": "scrape /metrics", "protocol": "http"},
]


def _container_status(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Aplana el estado de un container al subconjunto que necesita el grafo."""
    if detail is None:
        return {
            "running": False,
            "state": None,
            "health": None,
            "restart_count": 0,
            "started_at": None,
            "uptime_ms": None,
            "image": None,
            "networks": [],
            "ports": [],
        }
    state = detail.get("State") or {}
    started_at_ms = _parse_started_at(state.get("StartedAt"))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    uptime_ms = (now_ms - started_at_ms) if started_at_ms else None
    health = (state.get("Health") or {}).get("Status")
    # ports flat (igual al endpoint /containers)
    ports_raw = (detail.get("NetworkSettings", {}) or {}).get("Ports") or {}
    ports: list[dict[str, Any]] = []
    for port_key, bindings in ports_raw.items():
        private_port = int(port_key.split("/")[0])
        port_type = port_key.split("/")[1] if "/" in port_key else "tcp"
        if bindings:
            for b in bindings:
                ports.append({
                    "host_ip": b.get("HostIp") or None,
                    "host_port": int(b["HostPort"]) if b.get("HostPort") else None,
                    "container_port": private_port,
                    "type": port_type,
                })
        else:
            ports.append({
                "host_ip": None,
                "host_port": None,
                "container_port": private_port,
                "type": port_type,
            })
    return {
        "running": bool(state.get("Running")),
        "state": state.get("Status"),
        "health": health,
        "restart_count": detail.get("RestartCount") or 0,
        "started_at": started_at_ms,
        "uptime_ms": uptime_ms,
        "image": detail.get("Config", {}).get("Image") or detail.get("Image"),
        "networks": _network_summary(detail.get("NetworkSettings")),
        "ports": ports,
    }


@router.get("/topology")
async def topology(current: CurrentUser) -> dict[str, Any]:
    """HU-46 — Grafo del stack productivo: nodes (containers + cloudflare) + edges."""
    _require_admin(current)

    docker = aiodocker.Docker()
    try:
        # Indexar containers vivos por nombre (sin slash inicial).
        raw = await docker.containers.list(all=True)
        by_name: dict[str, dict[str, Any]] = {}
        for c in raw:
            try:
                detail = await c.show()
            except Exception:  # noqa: BLE001
                detail = c._container
            name = (detail.get("Name") or "").lstrip("/")
            if name:
                by_name[name] = detail

        # Construir nodes desde el registry, enriqueciendo con datos vivos.
        nodes: list[dict[str, Any]] = []
        seen_containers: set[str] = set()
        for spec in _NODE_REGISTRY:
            container_name = spec.get("container")
            detail = by_name.get(container_name) if container_name else None
            if container_name and detail is not None:
                seen_containers.add(container_name)
            status = _container_status(detail)
            # Para el nodo external (cloudflare) no hay container — marcamos
            # status especial "external" sin probe.
            if container_name is None:
                node_status = "external"
            elif detail is None:
                node_status = "missing"
            elif not status["running"]:
                node_status = "stopped"
            elif status["health"] == "unhealthy":
                node_status = "unhealthy"
            elif status["health"] == "starting":
                node_status = "starting"
            else:
                node_status = "ok"

            nodes.append({
                "id": spec["id"],
                "label": spec["label"],
                "type": spec["type"],
                "column": spec["column"],
                "container_name": container_name,
                "status": node_status,
                "note": spec.get("note"),
                "default_ports": spec.get("default_ports") or [],
                "live": status,
            })

        # Containers vivos que NO están en el registry → extras.
        for name, detail in by_name.items():
            if name in seen_containers:
                continue
            status = _container_status(detail)
            nodes.append({
                "id": f"extra:{name}",
                "label": name,
                "type": "extra",
                "column": 5,  # columna fuera del flujo principal
                "container_name": name,
                "status": "unknown" if status["running"] else "stopped",
                "note": "Container vivo sin lugar en el grafo definido",
                "default_ports": [],
                "live": status,
            })

        # Edges del registry sin filtrar — si un endpoint está missing,
        # la edge sigue dibujada pero el front la pinta en gris.
        edges = [
            {
                "from": e["from"],
                "to": e["to"],
                "label": e["label"],
                "protocol": e["protocol"],
            }
            for e in _EDGE_REGISTRY
        ]

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return {
            "nodes": nodes,
            "edges": edges,
            "columns": [
                {"id": 0, "label": "Externo"},
                {"id": 1, "label": "Proxy"},
                {"id": 2, "label": "Aplicación"},
                {"id": 3, "label": "Datos"},
                {"id": 4, "label": "Observabilidad"},
                {"id": 5, "label": "Extras"},
            ],
            "server_time": now_ms,
        }
    finally:
        await docker.close()


# ─────────────────────────────────────────────────────────────────────────────
# HU-47 — Peticiones HTTP recientes (ring buffer Redis)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/requests")
async def requests(current: CurrentUser, limit: int = 100) -> dict[str, Any]:
    """HU-47 — Últimas N peticiones que capturó el middleware HTTP.

    El middleware se registra en `app/main.py` y publica a un ring buffer
    Redis (key `system:http_recent`, LPUSH + LTRIM 0 99). Skip de
    `/api/system/*` y `/metrics` para no contaminar el ring con
    auto-refresh del propio endpoint."""
    _require_admin(current)
    items = await http_recent.recent(limit=limit)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return {
        "items": items,
        "count": len(items),
        "max_items": http_recent.MAX_ITEMS,
        "server_time": now_ms,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HU-48 — Estado del worker arq
# ─────────────────────────────────────────────────────────────────────────────
#
# arq guarda toda su metadata en Redis bajo el prefijo `arq:*`. Las relevantes:
#   - arq:queue                  zset con jobs pendientes (zcard = profundidad)
#   - arq:queue:health-check     string con el último heartbeat del worker
#   - arq:in-progress:<job_id>   string set por arq mientras corre el job
#   - arq:job:<job_id>           pickle del job pendiente
#   - arq:result:<job_id>        pickle del resultado (TTL = keep_result)
#
# No deserializamos los pickles (rieguen issues de seguridad y de imports).
# Solo contamos keys y leemos el heartbeat (string plano).


# Formato del heartbeat de arq, ej:
#   "May-25 02:09:58 j_complete=0 j_failed=0 j_retried=0 j_ongoing=0 queued=0"
_HB_RE = re.compile(
    r"^(?P<when>\S+ \S+) "
    r"j_complete=(?P<complete>\d+) "
    r"j_failed=(?P<failed>\d+) "
    r"j_retried=(?P<retried>\d+) "
    r"j_ongoing=(?P<ongoing>\d+) "
    r"queued=(?P<queued>\d+)"
)


def _parse_heartbeat(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    m = _HB_RE.match(raw.strip())
    if not m:
        return {"raw": raw, "parsed": False}
    g = m.groupdict()
    return {
        "raw": raw,
        "parsed": True,
        "when_str": g["when"],
        "j_complete": int(g["complete"]),
        "j_failed": int(g["failed"]),
        "j_retried": int(g["retried"]),
        "j_ongoing": int(g["ongoing"]),
        "queued": int(g["queued"]),
    }


def _worker_settings_meta() -> dict[str, Any]:
    """Lee WorkerSettings de `app.workers.tasks` sin instanciar el worker.
    Nombres de funciones + parámetros importantes para defensa de demo."""
    from app.workers.tasks import WorkerSettings  # import local — evita ciclo
    functions = []
    for fn in getattr(WorkerSettings, "functions", []) or []:
        functions.append({
            "name": getattr(fn, "__name__", str(fn)),
            "module": getattr(fn, "__module__", None),
            "doc": (fn.__doc__ or "").strip().split("\n")[0] if fn.__doc__ else None,
        })
    return {
        "functions": functions,
        "max_tries": getattr(WorkerSettings, "max_tries", None),
        "job_timeout": getattr(WorkerSettings, "job_timeout", None),
        "keep_result": getattr(WorkerSettings, "keep_result", None),
        "health_check_interval": getattr(WorkerSettings, "health_check_interval", None),
    }


# ─────────────────────────────────────────────────────────────────────────────
# HU-49 — Version/build info (público — útil para debug externo)
# ─────────────────────────────────────────────────────────────────────────────


async def _runtime_versions() -> dict[str, Any]:
    """Lee versiones reales de postgres y redis vía clientes ya configurados."""
    info: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    # Postgres — connection.execute("select version()")
    try:
        async with SessionLocal() as db:
            row = (await db.execute(text("SELECT version()"))).scalar()
            info["postgres"] = (row or "").split(" on ")[0]  # truncar parte del host
    except Exception as e:  # noqa: BLE001
        info["postgres"] = f"err: {e}"
    # Redis
    try:
        redis = _get_redis()
        rinfo = await redis.info("server")
        info["redis"] = rinfo.get("redis_version")
    except Exception as e:  # noqa: BLE001
        info["redis"] = f"err: {e}"
    return info


@router.get("/version")
async def version() -> dict[str, Any]:
    """HU-49 — Build info + versiones de runtime. **Público** (sin auth).

    GIT_COMMIT y BUILD_TIME se inyectan en build-time via Dockerfile ARG.
    Si no se setearon (build local sin script), aparece `unknown`."""
    git_commit = os.environ.get("GIT_COMMIT", "unknown")
    build_time = os.environ.get("BUILD_TIME", "unknown")
    runtime = await _runtime_versions()
    return {
        "service": "cognipilot-back",
        "git_commit": git_commit,
        "git_commit_short": git_commit[:7] if git_commit != "unknown" else "unknown",
        "build_time": build_time,
        "runtime": runtime,
        "server_time": int(datetime.now(timezone.utc).timestamp() * 1000),
    }


@router.get("/worker")
async def worker(current: CurrentUser) -> dict[str, Any]:
    """HU-48 — Estado del worker arq: queue depth, in-progress, heartbeat,
    functions registradas, conteos de resultados recientes."""
    _require_admin(current)
    redis = _get_redis()

    # Contadores en paralelo via pipeline (1 roundtrip).
    async with redis.pipeline(transaction=False) as pipe:
        pipe.zcard("arq:queue")
        pipe.get("arq:queue:health-check")
        pipe.keys("arq:in-progress:*")
        pipe.keys("arq:result:*")
        pipe.keys("arq:job:*")
        results = await pipe.execute()

    queue_depth, hb_raw, in_progress_keys, result_keys, job_keys = results
    heartbeat = _parse_heartbeat(hb_raw)
    settings_meta = _worker_settings_meta()

    # Health: si hay heartbeat reciente (< 3× health_check_interval), worker vivo.
    hc_interval = settings_meta.get("health_check_interval") or 30
    is_alive = heartbeat is not None  # arq sobreescribe el key cada health_check_interval

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return {
        "alive": is_alive,
        "queue_depth": int(queue_depth or 0),
        "in_progress_count": len(in_progress_keys or []),
        "result_count": len(result_keys or []),
        "pending_job_count": len(job_keys or []),
        "heartbeat": heartbeat,
        "settings": settings_meta,
        "redis_keys_arq_total": (
            (1 if hb_raw else 0)
            + (1 if queue_depth else 0)
            + len(in_progress_keys or [])
            + len(result_keys or [])
            + len(job_keys or [])
        ),
        "health_check_interval_s": hc_interval,
        "server_time": now_ms,
    }
