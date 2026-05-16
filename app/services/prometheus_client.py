"""Cliente HTTP para queries a la API de Prometheus.

Usado por `/api/metrics/timeseries` para devolverle al admin UI puntos
graficables. Si Prometheus no está corriendo, devuelve listas vacías
con un warning en logs (degradación graceful).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class PrometheusClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=5.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def query(self, expr: str) -> dict[str, Any] | None:
        """Instant query — un valor por serie."""
        try:
            r = await self._client.get(
                f"{self.base_url}/api/v1/query", params={"query": expr}
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "success":
                logger.warning("Prometheus query failed: %s", data)
                return None
            return data["data"]
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Prometheus unreachable: %s", e)
            return None

    async def query_range(
        self,
        expr: str,
        *,
        start_ts: float,
        end_ts: float,
        step_seconds: int,
    ) -> list[tuple[int, float]]:
        """Range query — lista de (ts, value) para graficar.

        Si Prometheus no responde, devuelve lista vacía.
        """
        try:
            r = await self._client.get(
                f"{self.base_url}/api/v1/query_range",
                params={
                    "query": expr,
                    "start": str(start_ts),
                    "end": str(end_ts),
                    "step": f"{step_seconds}s",
                },
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "success":
                logger.warning("Prometheus query_range failed: %s", data)
                return []
            result = data["data"]["result"]
            if not result:
                return []
            # Tomamos la primera serie (asumimos que la expresión devuelve una sola).
            values = result[0]["values"]
            return [(int(float(t)), float(v)) for t, v in values]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            logger.warning("Prometheus unreachable: %s", e)
            return []


_client_singleton: PrometheusClient | None = None


def get_prometheus_client() -> PrometheusClient:
    """Singleton del cliente. Inicializado lazy."""
    global _client_singleton
    if _client_singleton is None:
        url = get_settings().prometheus_url or "http://prometheus:9090"
        _client_singleton = PrometheusClient(url)
    return _client_singleton


def now_ts() -> float:
    return time.time()
