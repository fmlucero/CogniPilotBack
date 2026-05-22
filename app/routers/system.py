"""HU-45..HU-49 — Endpoints de inspección de infraestructura.

Todos admin_sistema only. Se agrupan acá para que la sección /sistema del
front tenga un único namespace `/api/system/*`.

Recursos cubiertos:
  - GET /api/system/containers   HU-45: inventario completo via docker socket
  - GET /api/system/topology     HU-46: nodes + edges para el grafo
  - GET /api/system/requests     HU-47: últimas N peticiones HTTP (Redis ring)
  - GET /api/system/worker       HU-48: estado del worker arq
  - GET /api/system/version      HU-49: build/git/runtime info (este es público)

Por ahora se implementa solo HU-45. Las demás se irán sumando.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiodocker
from fastapi import APIRouter, HTTPException

from app.core.deps import CurrentUser

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
