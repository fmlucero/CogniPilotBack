"""Firebase Admin para FCM push — port de cognipilot-remote/lib/firebase-admin.ts.

Inicialización lazy + thread-safe. Usado tanto desde el API (envío sync de fallback)
como desde el worker arq (envío async, recomendado).
"""
from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING

import time

import firebase_admin
from firebase_admin import credentials, messaging

from app.core.config import get_settings
from app.core.observability import fcm_push_duration_seconds, fcm_push_total

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()
_initialized = False


def _ensure_initialized() -> None:
    """Initialize firebase_admin app once."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        raw = get_settings().firebase_service_account_json
        if not raw:
            raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON env var is missing")
        try:
            service_account = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON: {e}") from e
        cred = credentials.Certificate(service_account)
        firebase_admin.initialize_app(cred)
        _initialized = True


def send_schedule_push(*, enabled: bool, time_from: str, time_to: str, tz: str) -> str:
    """Send a schedule update push to topic "schedule-updates".

    Sends notification + data combo:
      - notification: shown by system tray even if app is killed
      - data: delivered to onMessageReceived for live state update

    Returns the FCM message ID.
    """
    _ensure_initialized()

    title = "📢 Horario actualizado por supervisor"
    body = (
        f"Nuevo rango permitido: {time_from} – {time_to}"
        if enabled
        else "Restricción horaria desactivada"
    )

    message = messaging.Message(
        topic="schedule-updates",
        notification=messaging.Notification(title=title, body=body),
        data={
            "type": "schedule_update",
            "enabled": "true" if enabled else "false",
            "timeFrom": time_from,
            "timeTo": time_to,
            "tz": tz,
        },
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id="schedule_updates_channel",
                default_sound=True,
            ),
        ),
    )

    t0 = time.perf_counter()
    try:
        message_id = messaging.send(message)
    except Exception:
        fcm_push_total.labels(result="error").inc()
        fcm_push_duration_seconds.observe(time.perf_counter() - t0)
        raise
    fcm_push_total.labels(result="success").inc()
    fcm_push_duration_seconds.observe(time.perf_counter() - t0)
    logger.info("FCM push sent: %s", message_id)
    return message_id
