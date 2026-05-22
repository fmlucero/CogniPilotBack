"""SQLAlchemy 2.0 ORM models — espejo del schema Prisma del back viejo.

Las tablas tienen los MISMOS nombres y columnas que en `cognipilot-remote/prisma/schema.prisma`,
así Alembic puede tomar ownership de la DB existente con un baseline sin tocar datos.

Importar todos los modelos acá para que SQLAlchemy los registre en Base.metadata,
necesario para que Alembic autogenere migraciones.
"""
from app.models.alerta import Alerta
from app.models.audit import AuditEvent
from app.models.empresa import Empresa
from app.models.reset_password import ResetPasswordRequest
from app.models.system_setting import SystemSetting
from app.models.enums import AccionRegla, Rol, TipoEvento, TipoRegla
from app.models.eventos import EventoApp, Incidente, Posicion
from app.models.operacion import Asignacion, Paquete, Parada, Ruta
from app.models.regla import Regla, ReglaHistorial
from app.models.usuario import Dispositivo, Usuario

__all__ = [
    "Alerta",
    "AuditEvent",
    "Empresa",
    "ResetPasswordRequest",
    "SystemSetting",
    "Usuario",
    "Dispositivo",
    "Ruta",
    "Parada",
    "Paquete",
    "Asignacion",
    "Regla",
    "ReglaHistorial",
    "EventoApp",
    "Incidente",
    "Posicion",
    "AccionRegla",
    "Rol",
    "TipoEvento",
    "TipoRegla",
]
