"""CogniPilot — seed con dataset rico para demos y testing HU-03.

Dos empresas con sus respectivos usuarios/rutas/reglas:
  - "Logística Cuyo SA" (Mendoza): 1 supervisor, 1 gerente, 3 repartidores
  - "Transportes del Sur" (Bariloche): 1 supervisor, 1 gerente, 2 repartidores

Cada repartidor con asignación de ruta para HOY (zona horaria AR) y dispositivo
seedeado. Reglas activas variadas en ambas empresas.

Idempotente: usa upsert manual (SELECT + INSERT) por unique keys.

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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.empresa import Empresa
from app.models.enums import AccionRegla, Rol, TipoRegla
from app.models.operacion import Asignacion, Paquete, Parada, Ruta
from app.models.regla import Regla
from app.models.usuario import Dispositivo, Usuario

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _require_pw_env(name: str) -> str:
    v = os.getenv(name, "")
    if len(v) < 8:
        sys.exit(f"❌ Env var {name} must be set with at least 8 chars before seeding")
    return v


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ParadaSpec:
    orden: int
    lat: str
    lng: str
    direccion: str
    ventana_desde: str
    ventana_hasta: str
    paquetes: list[tuple[str, str]]  # [(codigoMl, descripcion), ...]


async def _upsert_empresa(db: AsyncSession, cuit: str, nombre: str, contacto: dict) -> Empresa:
    e = (await db.execute(select(Empresa).where(Empresa.cuit == cuit))).scalar_one_or_none()
    if e is None:
        e = Empresa(nombre=nombre, cuit=cuit, contacto=contacto)
        db.add(e)
        await db.flush()
    return e


async def _upsert_user(
    db: AsyncSession,
    email: str,
    nombre: str,
    rol: Rol,
    pw: str,
    empresa_id: str | None,
) -> Usuario:
    u = (await db.execute(select(Usuario).where(Usuario.email == email))).scalar_one_or_none()
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


async def _upsert_dispositivo(
    db: AsyncSession, usuario_id: str, device_uuid: str, modelo: str
) -> None:
    d = (
        await db.execute(select(Dispositivo).where(Dispositivo.deviceUuid == device_uuid))
    ).scalar_one_or_none()
    if d is None:
        db.add(
            Dispositivo(
                usuarioId=usuario_id,
                deviceUuid=device_uuid,
                modelo=modelo,
                osVersion="Android 14",
                appVersion="0.2.0-seed",
            )
        )
    else:
        d.activo = True
        d.usuarioId = usuario_id  # rebind al usuario actual


async def _upsert_ruta_con_paradas(
    db: AsyncSession,
    empresa_id: str,
    nombre: str,
    fecha_ruta: date,
    paradas: list[ParadaSpec],
) -> Ruta:
    # Idempotencia por (empresa, nombre, fecha). Si existe, no la duplicamos.
    existing = (
        await db.execute(
            select(Ruta).where(
                Ruta.empresaId == empresa_id,
                Ruta.nombre == nombre,
                Ruta.fecha == fecha_ruta,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    ruta = Ruta(empresaId=empresa_id, nombre=nombre, fecha=fecha_ruta)
    db.add(ruta)
    await db.flush()

    for p in paradas:
        parada_id = str(uuid.uuid4())
        db.add(
            Parada(
                id=parada_id,
                rutaId=ruta.id,
                orden=p.orden,
                lat=Decimal(p.lat),
                lng=Decimal(p.lng),
                direccion=p.direccion,
                ventanaDesde=p.ventana_desde,
                ventanaHasta=p.ventana_hasta,
            )
        )
        db.add_all(
            [
                Paquete(paradaId=parada_id, codigoMl=cod, descripcion=desc)
                for cod, desc in p.paquetes
            ]
        )
    return ruta


async def _upsert_asignacion(
    db: AsyncSession, repartidor_id: str, ruta_id: str, fecha_asig: date
) -> None:
    existing = (
        await db.execute(
            select(Asignacion).where(
                Asignacion.repartidorId == repartidor_id,
                Asignacion.fecha == fecha_asig,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(Asignacion(repartidorId=repartidor_id, rutaId=ruta_id, fecha=fecha_asig))
    else:
        existing.rutaId = ruta_id  # rebind si ya había una asignación distinta


async def _ensure_reglas(db: AsyncSession, empresa_id: str, reglas_spec: list[dict]) -> None:
    existing = (
        await db.execute(select(Regla).where(Regla.empresaId == empresa_id))
    ).scalars().all()
    if existing:
        return  # ya hay reglas para la empresa — no tocamos
    db.add_all([Regla(empresaId=empresa_id, **r) for r in reglas_spec])


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    logger.info("🌱 Seeding CogniPilot (dataset rico)...")

    admin_pw = _require_pw_env("SEED_ADMIN_PASSWORD")
    supervisor_pw = _require_pw_env("SEED_SUPERVISOR_PASSWORD")
    gerente_pw = _require_pw_env("SEED_GERENTE_PASSWORD")
    repartidor_pw = _require_pw_env("SEED_REPARTIDOR_PASSWORD")

    hoy = datetime.now(_AR_TZ).date()
    ayer = hoy - timedelta(days=1)

    async with SessionLocal() as db:
        # ── Admin del sistema (sin empresa) ──────────────────────────────────
        admin = await _upsert_user(
            db, "facu@cognipilot.local", "Facundo Lucero", Rol.admin_sistema, admin_pw, None
        )

        # ╭───────────────────────────────────────────────────────────────────╮
        # │ Empresa 1: Logística Cuyo SA (Mendoza)                            │
        # ╰───────────────────────────────────────────────────────────────────╯
        cuyo = await _upsert_empresa(
            db,
            cuit="30-71234567-1",
            nombre="Logística Cuyo SA",
            contacto={
                "email": "contacto@logisticacuyo.com.ar",
                "telefono": "+54 261 555 0100",
                "direccion": "San Martín 1234, Mendoza",
            },
        )

        sup_cuyo = await _upsert_user(
            db, "supervisor@logisticacuyo.com.ar", "Ana Bermúdez",
            Rol.supervisor, supervisor_pw, cuyo.id,
        )
        ger_cuyo = await _upsert_user(
            db, "gerente@logisticacuyo.com.ar", "Roberto Páez",
            Rol.gerente, gerente_pw, cuyo.id,
        )
        rep_facu = await _upsert_user(
            db, "fm.lucero@alumno.um.edu.ar", "Facu Lucero",
            Rol.repartidor, repartidor_pw, cuyo.id,
        )
        rep_diego = await _upsert_user(
            db, "diego.morales@logisticacuyo.com.ar", "Diego Morales",
            Rol.repartidor, repartidor_pw, cuyo.id,
        )
        rep_luciana = await _upsert_user(
            db, "luciana.varela@logisticacuyo.com.ar", "Luciana Varela",
            Rol.repartidor, repartidor_pw, cuyo.id,
        )

        # Dispositivos
        await _upsert_dispositivo(db, rep_facu.id, "dev-seed-facu-personal", "Pixel 7")
        await _upsert_dispositivo(db, rep_diego.id, "dev-seed-diego-moto", "Moto G54")
        await _upsert_dispositivo(db, rep_luciana.id, "dev-seed-luciana-samsung", "Samsung A24")

        # Rutas — 3 hoy (1 por cada repartidor) + 1 ayer
        ruta_centro = await _upsert_ruta_con_paradas(
            db, cuyo.id, "Ciudad — Godoy Cruz", hoy,
            [
                ParadaSpec(1, "-32.889500", "-68.845800",
                           "Av. San Martín 850, Mendoza", "09:00", "12:00",
                           [("ML-2025-0001", "Caja chica electrónica"),
                            ("ML-2025-0002", "Sobre documentos")]),
                ParadaSpec(2, "-32.907700", "-68.853800",
                           "Belgrano 1290, Mendoza", "10:00", "13:00",
                           [("ML-2025-0003", "Caja media indumentaria")]),
                ParadaSpec(3, "-32.929300", "-68.842100",
                           "Hipólito Yrigoyen 220, Godoy Cruz", "11:00", "14:00",
                           [("ML-2025-0004", "Caja grande electrodoméstico"),
                            ("ML-2025-0005", "Sobre tarjetas"),
                            ("ML-2025-0006", "Caja libros")]),
            ],
        )
        ruta_lashera = await _upsert_ruta_con_paradas(
            db, cuyo.id, "Las Heras — Centro", hoy,
            [
                ParadaSpec(1, "-32.849200", "-68.825300",
                           "San Miguel 540, Las Heras", "09:00", "12:00",
                           [("ML-2025-0020", "Caja media textil")]),
                ParadaSpec(2, "-32.885300", "-68.837800",
                           "Patricias Mendocinas 1456, Mendoza", "10:00", "13:00",
                           [("ML-2025-0021", "Sobre documentos"),
                            ("ML-2025-0022", "Caja chica electrónica")]),
                ParadaSpec(3, "-32.892100", "-68.846200",
                           "Lavalle 380, Mendoza", "11:30", "14:30",
                           [("ML-2025-0023", "Caja media electrodoméstico")]),
            ],
        )
        ruta_maipu = await _upsert_ruta_con_paradas(
            db, cuyo.id, "Maipú — Luján", hoy,
            [
                ParadaSpec(1, "-32.972100", "-68.789400",
                           "Pueyrredón 1100, Maipú", "08:30", "11:30",
                           [("ML-2025-0030", "Caja grande vinos")]),
                ParadaSpec(2, "-33.040800", "-68.876900",
                           "San Martín Sur 450, Luján", "10:00", "13:00",
                           [("ML-2025-0031", "Sobre documentos legales"),
                            ("ML-2025-0032", "Caja chica perfumería")]),
            ],
        )
        ruta_centro_ayer = await _upsert_ruta_con_paradas(
            db, cuyo.id, "Centro Histórico", ayer,
            [
                ParadaSpec(1, "-32.890500", "-68.844200",
                           "Av. España 1234, Mendoza", "09:00", "12:00",
                           [("ML-2025-9001", "Caja libros usados"),
                            ("ML-2025-9002", "Sobre documentos")]),
            ],
        )

        # Asignaciones
        await _upsert_asignacion(db, rep_facu.id, ruta_centro.id, hoy)
        await _upsert_asignacion(db, rep_diego.id, ruta_lashera.id, hoy)
        await _upsert_asignacion(db, rep_luciana.id, ruta_maipu.id, hoy)
        # facu también tuvo asignación ayer (para datos históricos)
        await _upsert_asignacion(db, rep_facu.id, ruta_centro_ayer.id, ayer)

        # Reglas (solo si no hay ninguna para la empresa)
        await _ensure_reglas(db, cuyo.id, [
            dict(
                nombre="Ventana horaria estándar 08:00–18:00 ART",
                tipo=TipoRegla.ventana_horaria,
                accion=AccionRegla.bloquear,
                condicion={
                    "desde": "08:00", "hasta": "18:00",
                    "tz": "America/Argentina/Buenos_Aires",
                },
            ),
            dict(
                nombre="Paquete fuera de parada (Poka-Yoke)",
                tipo=TipoRegla.paquete_fuera_parada,
                accion=AccionRegla.bloquear,
                condicion={},
            ),
            dict(
                nombre="Bloquear redes sociales en horario laboral",
                tipo=TipoRegla.app_bloqueada_en_horario,
                accion=AccionRegla.bloquear,
                condicion={
                    "packages": [
                        "com.instagram.android",
                        "com.facebook.katana",
                        "com.zhiliaoapp.musically",
                        "com.twitter.android",
                    ],
                    "desde": "08:00", "hasta": "18:00",
                    "tz": "America/Argentina/Buenos_Aires",
                },
            ),
        ])

        # ╭───────────────────────────────────────────────────────────────────╮
        # │ Empresa 2: Transportes del Sur SRL (Bariloche)                    │
        # ╰───────────────────────────────────────────────────────────────────╯
        sur = await _upsert_empresa(
            db,
            cuit="30-71987654-2",
            nombre="Transportes del Sur SRL",
            contacto={
                "email": "operaciones@transportesdelsur.com.ar",
                "telefono": "+54 294 442 0200",
                "direccion": "Mitre 320, Bariloche",
            },
        )

        sup_sur = await _upsert_user(
            db, "supervisor@transportesdelsur.com.ar", "Mariana Quintero",
            Rol.supervisor, supervisor_pw, sur.id,
        )
        ger_sur = await _upsert_user(
            db, "gerente@transportesdelsur.com.ar", "Esteban Núñez",
            Rol.gerente, gerente_pw, sur.id,
        )
        rep_javi = await _upsert_user(
            db, "javier.rios@transportesdelsur.com.ar", "Javier Ríos",
            Rol.repartidor, repartidor_pw, sur.id,
        )
        rep_carla = await _upsert_user(
            db, "carla.guzman@transportesdelsur.com.ar", "Carla Guzmán",
            Rol.repartidor, repartidor_pw, sur.id,
        )

        await _upsert_dispositivo(db, rep_javi.id, "dev-seed-javi-pixel", "Pixel 6a")
        await _upsert_dispositivo(db, rep_carla.id, "dev-seed-carla-xiaomi", "Xiaomi Redmi Note 12")

        ruta_centro_brc = await _upsert_ruta_con_paradas(
            db, sur.id, "Centro Bariloche", hoy,
            [
                ParadaSpec(1, "-41.133500", "-71.310800",
                           "Mitre 450, Bariloche", "09:00", "12:00",
                           [("ML-2025-5001", "Caja chica electrónica")]),
                ParadaSpec(2, "-41.139200", "-71.305100",
                           "Moreno 890, Bariloche", "10:30", "13:30",
                           [("ML-2025-5002", "Caja media textil"),
                            ("ML-2025-5003", "Sobre documentos")]),
            ],
        )
        ruta_dina = await _upsert_ruta_con_paradas(
            db, sur.id, "Dina Huapi", hoy,
            [
                ParadaSpec(1, "-41.067800", "-71.169300",
                           "Av. Los Pioneros 1200, Dina Huapi", "10:00", "13:00",
                           [("ML-2025-5010", "Caja grande electrodoméstico"),
                            ("ML-2025-5011", "Caja media indumentaria")]),
            ],
        )

        await _upsert_asignacion(db, rep_javi.id, ruta_centro_brc.id, hoy)
        await _upsert_asignacion(db, rep_carla.id, ruta_dina.id, hoy)

        # Reglas para esta empresa
        await _ensure_reglas(db, sur.id, [
            dict(
                nombre="Ventana horaria invernal 09:00–17:00 ART",
                tipo=TipoRegla.ventana_horaria,
                accion=AccionRegla.alertar,
                condicion={
                    "desde": "09:00", "hasta": "17:00",
                    "tz": "America/Argentina/Buenos_Aires",
                },
            ),
            dict(
                nombre="Paquete fuera de parada (alerta, no bloqueo)",
                tipo=TipoRegla.paquete_fuera_parada,
                accion=AccionRegla.alertar,
                condicion={},
            ),
        ])

        await db.commit()

        logger.info("✅ Seed completo:")
        logger.info("   Admin: %s", admin.email)
        logger.info("   Empresa 1: %s", cuyo.nombre)
        logger.info("     Supervisor: %s", sup_cuyo.email)
        logger.info("     Gerente:    %s", ger_cuyo.email)
        logger.info("     Repartidores: %s, %s, %s",
                    rep_facu.email, rep_diego.email, rep_luciana.email)
        logger.info("     Rutas hoy: %s, %s, %s",
                    ruta_centro.nombre, ruta_lashera.nombre, ruta_maipu.nombre)
        logger.info("   Empresa 2: %s", sur.nombre)
        logger.info("     Supervisor: %s", sup_sur.email)
        logger.info("     Gerente:    %s", ger_sur.email)
        logger.info("     Repartidores: %s, %s", rep_javi.email, rep_carla.email)
        logger.info("     Rutas hoy: %s, %s", ruta_centro_brc.nombre, ruta_dina.nombre)


if __name__ == "__main__":
    asyncio.run(main())
