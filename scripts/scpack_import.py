"""Parser + importador de rutas capturadas de SC Pack (modo exploración).

Toma las capturas `SNAP:*` que la app dejó en `EventoApp` para un repartidor,
parsea las paradas (orden / dirección / barrio / ventana horaria / unidades a
colectar), geocodifica las direcciones (Nominatim, sesgo AR/CABA) y materializa
una **Ruta + Paradas + Asignación** para HOY. Así el repartidor ve su ruta real
en la app y se pueden probar los bloqueos de horario/geocerca contra datos
reales, sin cargarla a mano.

El parser es tolerante a que cada captura venga truncada (tope de líneas del
capturador): se mergean varias capturas por número de parada (`badge`), llenando
los campos faltantes entre una y otra.

Correr (dentro del container del back):
    docker compose exec -T back-api python -m scripts.scpack_import --user nico@cognipilot.com
    # opciones: --fecha YYYY-MM-DD  --lookback-min 240  --nombre "SC Pack ..."  --dry-run  --no-paquetes
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date as Date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models.eventos import EventoApp
from app.models.operacion import Asignacion, Paquete, Parada, Ruta
from app.models.usuario import Usuario

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("scpack_import")

_AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# ─────────────────────────────────────────────────────────────────────────────
# Parser de capturas
# ─────────────────────────────────────────────────────────────────────────────

# Línea de captura: "[depth|Clase|viewId] texto ¦cd:contentDesc ¦clk"
_LINE_RE = re.compile(r"^\[(\d+)\|([^|]*)\|([^\]]*)\]\s*(.*)$")
_TIME_RE = re.compile(r"(\d{1,2}:\d{2})")
_UNITS_RE = re.compile(r"([A-Za-zÁÉÍÓÚáéíóúÑñ]+)\s+(\d+)\s+unidad", re.IGNORECASE)
_TOTAL_RE = re.compile(r"(\d+)\s+paradas", re.IGNORECASE)


@dataclass
class Stop:
    orden: int
    direccion: str | None = None
    barrio: str | None = None
    ventana_desde: str | None = None
    ventana_hasta: str | None = None
    accion: str | None = None
    unidades: int | None = None
    lat: Decimal | None = None
    lng: Decimal | None = None

    def merge_from(self, other: "Stop") -> None:
        """Rellena los campos vacíos con los de otra captura del mismo orden."""
        for f in ("direccion", "barrio", "ventana_desde", "ventana_hasta", "accion", "unidades"):
            if getattr(self, f) in (None, "") and getattr(other, f) not in (None, ""):
                setattr(self, f, getattr(other, f))


def _parse_line(line: str) -> tuple[str, str, str] | None:
    """Devuelve (viewId, texto, contentDesc) o None si la línea no matchea."""
    m = _LINE_RE.match(line)
    if not m:
        return None
    view_id = m.group(3).strip()
    rest = m.group(4)
    cd = ""
    if "¦cd:" in rest:
        cd = rest.split("¦cd:", 1)[1]
        cd = cd.split("¦", 1)[0].strip().rstrip(".")
    text = rest.split("¦", 1)[0].strip()
    return view_id, text, cd


def _parse_schedule(raw: str) -> tuple[str | None, str | None]:
    """'14:35hs a 15:05hs.' | '14:35 - 15:05.' → ('14:35', '15:05')."""
    times = _TIME_RE.findall(raw or "")
    desde = times[0] if len(times) >= 1 else None
    hasta = times[1] if len(times) >= 2 else None
    return desde, hasta


def _parse_units(text: str) -> tuple[str | None, int | None]:
    """'Colecta 3 unidades' → ('Colecta', 3)."""
    m = _UNITS_RE.search(text or "")
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def parse_captures(captures: list[list[str]]) -> tuple[dict[int, Stop], int | None]:
    """Parsea y mergea varias capturas → {orden: Stop} + total esperado (toolbar).

    Cada parada en la captura es un bloque que arranca en
    `listing_stops_row_schedule` y sigue con badge/title/subtitle/extra_info.
    """
    merged: dict[int, Stop] = {}
    expected_total: int | None = None

    for screen_text in captures:
        cur: Stop | None = None
        for line in screen_text:
            parsed = _parse_line(line)
            if parsed is None:
                continue
            view_id, text, cd = parsed

            if view_id == "flux_components_toolbar_title":
                mt = _TOTAL_RE.search(text)
                if mt:
                    expected_total = int(mt.group(1))
                continue

            if view_id == "listing_stops_row_schedule":
                # Cierra la parada anterior y arranca una nueva.
                _flush(cur, merged)
                cur = Stop(orden=-1)
                cur.ventana_desde, cur.ventana_hasta = _parse_schedule(cd or text)
            elif cur is not None:
                if view_id == "listing_stops_row_badge" and text.isdigit():
                    cur.orden = int(text)
                elif view_id == "listing_stops_row_title":
                    cur.direccion = text
                elif view_id == "listing_stops_row_subtitle":
                    cur.barrio = text
                elif view_id == "listing_stops_row_extra_info":
                    cur.accion, cur.unidades = _parse_units(text)
        _flush(cur, merged)

    return merged, expected_total


def _flush(cur: Stop | None, merged: dict[int, Stop]) -> None:
    """Agrega/mergea una parada al dict si tiene orden y dirección válidos."""
    if cur is None or cur.orden < 0 or not cur.direccion:
        return
    if cur.orden in merged:
        merged[cur.orden].merge_from(cur)
    else:
        merged[cur.orden] = cur


# ─────────────────────────────────────────────────────────────────────────────
# Geocoding (Nominatim + fallback por barrio)
# ─────────────────────────────────────────────────────────────────────────────

# Centroides aproximados de barrios de CABA — fallback si Nominatim no resuelve.
_BARRIO_FALLBACK: dict[str, tuple[float, float]] = {
    "palermo": (-34.5780, -58.4300),
    "recoleta": (-34.5875, -58.3970),
    "balvanera": (-34.6100, -58.4050),
}
_CABA_CENTER = (-34.6037, -58.3816)

# Nominatim no resuelve si la calle viene con su tipo ("Calle Bulnes 1776" no
# matchea, "Bulnes 1776" sí — la calle es "Bulnes"). Se saca el prefijo.
_STREET_PREFIX_RE = re.compile(
    r"^(calle|avenida|av\.?|pasaje|pje\.?|diagonal|diag\.?|bulevar|blvd\.?)\s+",
    re.IGNORECASE,
)


def _clean_street(direccion: str) -> str:
    return _STREET_PREFIX_RE.sub("", direccion or "").strip()


def _geocode(direccion: str, barrio: str | None) -> tuple[Decimal, Decimal, bool]:
    """Devuelve (lat, lng, ok_real). ok_real=False si cayó al fallback."""
    query = ", ".join(filter(None, [_clean_street(direccion), barrio, "Buenos Aires", "Argentina"]))
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": "1", "countrycodes": "ar"}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CogniPilot-TIF/1.0 (piloto)"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data:
            return Decimal(str(data[0]["lat"])), Decimal(str(data[0]["lon"])), True
    except Exception as e:  # noqa: BLE001
        logger.warning("  geocode falló para '%s': %s", query, e)

    lat, lng = _BARRIO_FALLBACK.get((barrio or "").strip().lower(), _CABA_CENTER)
    return Decimal(str(lat)), Decimal(str(lng)), False


async def _geocode_stops(stops: list[Stop]) -> None:
    """Geocodifica cada parada (cachea por dirección para no repetir llamadas)."""
    cache: dict[str, tuple[Decimal, Decimal, bool]] = {}
    for i, s in enumerate(stops):
        key = f"{s.direccion}|{s.barrio}"
        if key not in cache:
            if i > 0:
                await asyncio.sleep(1.1)  # política de uso de Nominatim: 1 req/s
            cache[key] = await asyncio.to_thread(_geocode, s.direccion or "", s.barrio)
        s.lat, s.lng, ok = cache[key]
        logger.info(
            "  #%d %-32s %-12s %s-%s  → %s,%s%s",
            s.orden, (s.direccion or "")[:32], (s.barrio or "")[:12],
            s.ventana_desde, s.ventana_hasta, s.lat, s.lng,
            "" if ok else "  (fallback barrio)",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Materialización en DB
# ─────────────────────────────────────────────────────────────────────────────


async def _load_captures(
    db: AsyncSession, usuario_id: str, since: datetime
) -> list[list[str]]:
    rows = (
        await db.execute(
            select(EventoApp.screenText)
            .where(
                EventoApp.usuarioId == usuario_id,
                EventoApp.screenName.like("SNAP:%"),
                EventoApp.ts >= since,
            )
            .order_by(EventoApp.ts.asc())
        )
    ).all()
    return [r[0] for r in rows if r[0]]


async def _upsert_ruta(
    db: AsyncSession, empresa_id: str, fecha: Date, nombre: str, stops: list[Stop],
    con_paquetes: bool,
) -> Ruta:
    ruta = (
        await db.execute(
            select(Ruta).where(
                Ruta.empresaId == empresa_id, Ruta.fecha == fecha, Ruta.nombre == nombre
            )
        )
    ).scalar_one_or_none()

    if ruta is not None:
        # Re-run idempotente: borrar paradas (y sus paquetes) previas de esta ruta.
        parada_ids = (
            await db.execute(select(Parada.id).where(Parada.rutaId == ruta.id))
        ).scalars().all()
        if parada_ids:
            await db.execute(delete(Paquete).where(Paquete.paradaId.in_(parada_ids)))
            await db.execute(delete(Parada).where(Parada.id.in_(parada_ids)))
    else:
        ruta = Ruta(empresaId=empresa_id, nombre=nombre, fecha=fecha)
        db.add(ruta)
        await db.flush()

    for s in stops:
        parada = Parada(
            rutaId=ruta.id,
            orden=s.orden,
            lat=s.lat,
            lng=s.lng,
            direccion=s.direccion,
            ventanaDesde=s.ventana_desde,
            ventanaHasta=s.ventana_hasta,
        )
        db.add(parada)
        await db.flush()
        if con_paquetes and s.unidades:
            for i in range(s.unidades):
                db.add(Paquete(
                    paradaId=parada.id,
                    codigoMl=f"SC-{fecha:%Y%m%d}-{s.orden:02d}-{i:03d}",
                    descripcion=s.accion or "Colecta",
                ))
    return ruta


async def _upsert_asignacion(
    db: AsyncSession, repartidor_id: str, ruta_id: str, fecha: Date
) -> None:
    asig = (
        await db.execute(
            select(Asignacion).where(
                Asignacion.repartidorId == repartidor_id, Asignacion.fecha == fecha
            )
        )
    ).scalar_one_or_none()
    if asig is not None:
        asig.rutaId = ruta_id
    else:
        db.add(Asignacion(repartidorId=repartidor_id, rutaId=ruta_id, fecha=fecha))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> None:
    fecha = (
        datetime.strptime(args.fecha, "%Y-%m-%d").date()
        if args.fecha else datetime.now(_AR_TZ).date()
    )
    nombre = args.nombre or f"SC Pack {fecha:%d/%m/%Y}"

    async with SessionLocal() as db:
        # 1) Resolver el repartidor.
        usuario = (
            await db.execute(select(Usuario).where(Usuario.email == args.user))
        ).scalar_one_or_none()
        if usuario is None:
            sys.exit(f"❌ Usuario '{args.user}' no encontrado")
        if not usuario.empresaId:
            sys.exit(f"❌ El usuario '{args.user}' no tiene empresa asignada")
        logger.info("Repartidor: %s (%s) — empresa %s", usuario.nombre, usuario.email, usuario.empresaId)

        # 2) Cargar y parsear capturas.
        since = datetime.now(_AR_TZ) - timedelta(minutes=args.lookback_min)
        captures = await _load_captures(db, usuario.id, since)
        if not captures:
            sys.exit(f"❌ Sin capturas SNAP:* para {args.user} en los últimos {args.lookback_min} min")
        logger.info("Capturas leídas: %d (últimos %d min)", len(captures), args.lookback_min)

        merged, expected = parse_captures(captures)
        stops = [merged[k] for k in sorted(merged)]
        if not stops:
            sys.exit("❌ No se parseó ninguna parada de las capturas")
        logger.info("Paradas parseadas: %d%s", len(stops),
                    f" (la ruta declara {expected})" if expected else "")
        if expected and expected != len(stops):
            logger.warning("⚠️  Faltan paradas: parseadas %d de %d. Pedile al repartidor que"
                           " abra 'Lista' y scrollee para capturar el resto, y volvé a correr.",
                           len(stops), expected)

        # 3) Geocodificar.
        logger.info("Geocodificando %d paradas…", len(stops))
        await _geocode_stops(stops)

        if args.dry_run:
            logger.info("🔎 --dry-run: no se escribió nada en la DB.")
            return

        # 4) Materializar Ruta + Paradas (+ Paquetes) + Asignación.
        ruta = await _upsert_ruta(
            db, usuario.empresaId, fecha, nombre, stops, con_paquetes=not args.no_paquetes
        )
        await _upsert_asignacion(db, usuario.id, ruta.id, fecha)
        await db.commit()

        total_u = sum(s.unidades or 0 for s in stops)
        logger.info("✅ Ruta '%s' (%s) — %d paradas%s, asignada a %s para %s",
                    nombre, ruta.id, len(stops),
                    f", {total_u} unidades" if not args.no_paquetes else "",
                    usuario.nombre, fecha)


def main() -> None:
    p = argparse.ArgumentParser(description="Importa una ruta de SC Pack capturada por el modo exploración.")
    p.add_argument("--user", required=True, help="Email del repartidor (dueño de las capturas)")
    p.add_argument("--fecha", help="YYYY-MM-DD (default: hoy AR)")
    p.add_argument("--nombre", help="Nombre de la ruta (default: 'SC Pack DD/MM/YYYY')")
    p.add_argument("--lookback-min", type=int, default=240, help="Ventana de capturas a considerar (min)")
    p.add_argument("--no-paquetes", action="store_true", help="No crear Paquetes por unidad")
    p.add_argument("--dry-run", action="store_true", help="Parsear + geocodificar sin escribir en DB")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
