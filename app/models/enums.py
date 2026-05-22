"""Enums Postgres compartidos entre modelos.

Estos enums existen en la DB con los MISMOS nombres y valores que en Prisma:
   Rol, AccionRegla, TipoRegla, TipoEvento

Acá los espejamos. SQLAlchemy los usa con `Enum(..., name="...", create_type=False)`
para que Alembic NO los recree (ya están creados por Prisma).
"""
from __future__ import annotations

import enum


class Rol(str, enum.Enum):
    admin_sistema = "admin_sistema"
    supervisor = "supervisor"
    gerente = "gerente"
    repartidor = "repartidor"


class AccionRegla(str, enum.Enum):
    bloquear = "bloquear"
    alertar = "alertar"


class TipoRegla(str, enum.Enum):
    paquete_fuera_parada = "paquete_fuera_parada"
    ventana_horaria = "ventana_horaria"
    app_bloqueada_en_horario = "app_bloqueada_en_horario"
    geofence = "geofence"  # HU-42 — bloquea escaneos fuera de un radio (lat/lng/radius_m)


class TipoEvento(str, enum.Enum):
    app_opened = "app_opened"
    warning_shown = "warning_shown"
    scan_detected = "scan_detected"
    user_continued = "user_continued"
    user_cancelled = "user_cancelled"
    global_app_opened = "global_app_opened"
    global_clicked = "global_clicked"
