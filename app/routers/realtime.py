"""Endpoint Server-Sent Events para HU-18 fase 4.

GET /api/realtime/stream — el cliente abre una conexión HTTP de larga duración
y recibe eventos a medida que ocurren en el back. Reemplaza al push FCM.

Eventos emitidos:
  - event: schedule_updated
    data:  { "enabled": bool, "from": "HH:mm", "to": "HH:mm",
             "tz": "...", "updatedAt": <ms>, "updatedBy": "email|null" }

El cliente Android (RealtimeStreamClient con OkHttp EventSource) consume esto
en foreground. Si la conexión cae (red mala, app en bg), reconecta o cae al
polling del WorkManager.

Heartbeat cada 15s vía sse-starlette `ping` para mantener conexión viva
detrás de proxies que cierran sockets ociosos (nginx tiene proxy_read_timeout
30s en nuestra config — el ping mantiene la conexión).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.services.realtime import subscribe_schedule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/realtime", tags=["realtime"])


@router.get("/stream")
async def stream(request: Request) -> EventSourceResponse:
    """Suscribe el cliente al channel schedule de Redis y va emitiendo
    eventos SSE. Maneja desconexión limpia cuando el cliente cierra el socket.
    """

    async def event_generator() -> AsyncIterator[dict]:
        async for payload in subscribe_schedule():
            if await request.is_disconnected():
                logger.debug("SSE client disconnected, cerrando suscripción")
                break
            yield {
                "event": "schedule_updated",
                "data": json.dumps(payload),
            }

    return EventSourceResponse(
        event_generator(),
        ping=15,  # heartbeat cada 15s para keep-alive a través de nginx
    )
