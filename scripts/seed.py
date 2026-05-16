"""CogniPilot — seed inicial (port de cognipilot-remote/prisma/seed.ts).

Empresa, usuarios con roles, dispositivo del repartidor, rutas/paradas/paquetes
realistas en zona Mendoza, reglas activas.

Idempotente: usa upsert manual (SELECT + UPDATE/INSERT) por unique keys.

Correr:
    # Desde el container del back:
    docker compose run --rm back-api python -m scripts.seed

    # O local con uv:
    uv run python -m scripts.seed
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.empresa import Empresa
from app.models.enums import AccionRegla, Rol, TipoRegla
from app.models.operacion import Asignacion, Paquete, Parada, Ruta
from app.models.regla import Regla
from app.models.usuario import Dispositivo, Usuario

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _require_pw_env(name: str) -> str:
    v = os.getenv(name, "")
    if len(v) < 8:
        sys.exit(f"❌ Env var {name} must be set with at least 8 chars before seeding")
    return v


async def main() -> None:
    logger.info("🌱 Seeding CogniPilot...")

    admin_pw = _require_pw_env("SEED_ADMIN_PASSWORD")
    supervisor_pw = _require_pw_env("SEED_SUPERVISOR_PASSWORD")
    gerente_pw = _require_pw_env("SEED_GERENTE_PASSWORD")
    repartidor_pw = _require_pw_env("SEED_REPARTIDOR_PASSWORD")

    async with SessionLocal() as db:
        # ── Empresa ──────────────────────────────────────────────────────────
        empresa = (
            await db.execute(select(Empresa).where(Empresa.cuit == "30-71234567-1"))
        ).scalar_one_or_none()
        if empresa is None:
            empresa = Empresa(
                nombre="Logística Cuyo SA",
                cuit="30-71234567-1",
                contacto={
                    "email": "contacto@logisticacuyo.com.ar",
                    "telefono": "+54 261 555 0100",
                    "direccion": "San Martín 1234, Mendoza",
                },
            )
            db.add(empresa)
            await db.flush()

        # ── Usuarios ─────────────────────────────────────────────────────────
        async def _upsert_user(
            email: str, nombre: str, rol: Rol, pw: str, empresa_id: str | None
        ) -> Usuario:
            u = (
                await db.execute(select(Usuario).where(Usuario.email == email))
            ).scalar_one_or_none()
            if u is None:
                u = Usuario(
                    email=email,
                    nombre=nombre,
                    rol=rol,
                    empresaId=empresa_id,
                    passwordHash=hash_password(pw),
                )
                db.add(u)
                await db.flush()
            return u

        admin = await _upsert_user(
            "facu@cognipilot.local", "Facundo Lucero", Rol.admin_sistema, admin_pw, None
        )
        supervisor = await _upsert_user(
            "supervisor@logisticacuyo.com.ar",
            "Ana Bermúdez",
            Rol.supervisor,
            supervisor_pw,
            empresa.id,
        )
        gerente = await _upsert_user(
            "gerente@logisticacuyo.com.ar",
            "Roberto Páez",
            Rol.gerente,
            gerente_pw,
            empresa.id,
        )
        repartidor = await _upsert_user(
            "fm.lucero@alumno.um.edu.ar",
            "Facu (repartidor)",
            Rol.repartidor,
            repartidor_pw,
            empresa.id,
        )

        # ── Dispositivo del repartidor (1 personal) ──────────────────────────
        dev_uuid = "dev-seed-facu-personal"
        dev = (
            await db.execute(select(Dispositivo).where(Dispositivo.deviceUuid == dev_uuid))
        ).scalar_one_or_none()
        if dev is None:
            db.add(
                Dispositivo(
                    usuarioId=repartidor.id,
                    deviceUuid=dev_uuid,
                    modelo="Pixel/Sample",
                    osVersion="Android 14",
                    appVersion="0.1.0-seed",
                )
            )
        else:
            dev.activo = True

        # ── Rutas (1 hoy, 1 ayer) en zona Mendoza ────────────────────────────
        hoy = datetime.now(timezone.utc).date()
        ayer = hoy - timedelta(days=1)

        # Solo crear rutas si la empresa todavía no tiene ninguna (idempotencia simple)
        existing_rutas = (
            await db.execute(select(Ruta).where(Ruta.empresaId == empresa.id))
        ).scalars().all()

        if not existing_rutas:
            ruta_hoy = Ruta(
                empresaId=empresa.id, nombre="Ciudad — Godoy Cruz", fecha=hoy
            )
            db.add(ruta_hoy)
            await db.flush()
            _add_paradas(db, ruta_hoy.id, [
                (1, "-32.889500", "-68.845800", "Av. San Martín 850, Mendoza",
                 "09:00", "12:00",
                 [("ML-2025-0001", "Caja chica electrónica"),
                  ("ML-2025-0002", "Sobre documentos")]),
                (2, "-32.907700", "-68.853800", "Belgrano 1290, Mendoza",
                 "10:00", "13:00",
                 [("ML-2025-0003", "Caja media indumentaria")]),
                (3, "-32.929300", "-68.842100", "Hipólito Yrigoyen 220, Godoy Cruz",
                 "11:00", "14:00",
                 [("ML-2025-0004", "Caja grande electrodoméstico"),
                  ("ML-2025-0005", "Sobre tarjetas"),
                  ("ML-2025-0006", "Caja libros")]),
            ])

            ruta_ayer = Ruta(
                empresaId=empresa.id, nombre="Las Heras — Centro", fecha=ayer
            )
            db.add(ruta_ayer)
            await db.flush()
            _add_paradas(db, ruta_ayer.id, [
                (1, "-32.849200", "-68.825300", "San Miguel 540, Las Heras",
                 "09:00", "12:00",
                 [("ML-2025-0010", "Caja media")]),
                (2, "-32.885300", "-68.837800", "Patricias Mendocinas 1456, Mendoza",
                 "10:00", "13:00",
                 [("ML-2025-0011", "Sobre documentos"),
                  ("ML-2025-0012", "Caja chica")]),
            ])

            # Asignaciones
            for r, f in ((ruta_hoy, hoy), (ruta_ayer, ayer)):
                exists = (
                    await db.execute(
                        select(Asignacion).where(
                            Asignacion.repartidorId == repartidor.id,
                            Asignacion.fecha == f,
                        )
                    )
                ).scalar_one_or_none()
                if exists is None:
                    db.add(Asignacion(repartidorId=repartidor.id, rutaId=r.id, fecha=f))

        # ── Reglas activas (solo crear si no hay ninguna para la empresa) ────
        existing_reglas = (
            await db.execute(select(Regla).where(Regla.empresaId == empresa.id))
        ).scalars().all()
        if not existing_reglas:
            db.add_all([
                Regla(
                    empresaId=empresa.id,
                    nombre="Ventana horaria estándar 08:00–18:00 ART",
                    tipo=TipoRegla.ventana_horaria,
                    accion=AccionRegla.bloquear,
                    condicion={
                        "desde": "08:00",
                        "hasta": "18:00",
                        "tz": "America/Argentina/Buenos_Aires",
                    },
                ),
                Regla(
                    empresaId=empresa.id,
                    nombre="Paquete fuera de parada (Poka-Yoke)",
                    tipo=TipoRegla.paquete_fuera_parada,
                    accion=AccionRegla.bloquear,
                    condicion={},
                ),
                Regla(
                    empresaId=empresa.id,
                    nombre="Bloquear redes sociales en horario laboral",
                    tipo=TipoRegla.app_bloqueada_en_horario,
                    accion=AccionRegla.bloquear,
                    condicion={
                        "packages": [
                            "com.instagram.android",
                            "com.facebook.katana",
                            "com.zhiliaoapp.musically",  # TikTok
                            "com.twitter.android",
                            "com.whatsapp",  # ojo: WhatsApp puede ser laboral
                        ],
                        "desde": "08:00",
                        "hasta": "18:00",
                        "tz": "America/Argentina/Buenos_Aires",
                    },
                ),
            ])

        await db.commit()

        logger.info("✅ Seed completo:")
        logger.info("   Empresa: %s (%s)", empresa.nombre, empresa.id)
        logger.info("   Admin: %s", admin.email)
        logger.info("   Supervisor: %s", supervisor.email)
        logger.info("   Gerente: %s", gerente.email)
        logger.info("   Repartidor: %s", repartidor.email)
        logger.info("   Dispositivo seed UUID: %s", dev_uuid)


def _add_paradas(db, ruta_id: str, paradas: list) -> None:
    """Helper: agrega paradas + paquetes a una ruta. Recibe sesión activa.

    Pre-generamos los UUIDs para poder linkear los paquetes sin necesidad
    de hacer flush intermedios.
    """
    for orden, lat, lng, direccion, vd, vh, paquetes in paradas:
        parada_id = str(uuid.uuid4())
        db.add(
            Parada(
                id=parada_id,
                rutaId=ruta_id,
                orden=orden,
                lat=Decimal(lat),
                lng=Decimal(lng),
                direccion=direccion,
                ventanaDesde=vd,
                ventanaHasta=vh,
            )
        )
        db.add_all([
            Paquete(paradaId=parada_id, codigoMl=cod, descripcion=desc)
            for cod, desc in paquetes
        ])


if __name__ == "__main__":
    asyncio.run(main())
