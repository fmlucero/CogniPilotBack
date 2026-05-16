"""Helpers geoespaciales — distancia haversine en metros.

Usado por POST /api/positions para decidir si una nueva coordenada debe insertar
una fila en `Posicion` o solo actualizar `Dispositivo.lastLat/lastLng/lastSeen`.
"""
from __future__ import annotations

import math
from decimal import Decimal

# Radio terrestre medio en metros (WGS-84 esférico aproximado).
_EARTH_RADIUS_M = 6371000.0

# Umbral default: si la nueva posición difiere <X metros de la última, NO insertamos
# nueva fila en Posicion. Solo actualizamos los campos lastLat/lastLng/lastSeen
# en Dispositivo. Esto controla el crecimiento de la tabla cuando el repartidor
# está parado o se mueve muy poco.
DEFAULT_POSITION_THRESHOLD_M = 10.0


def haversine_meters(
    lat1: float | Decimal,
    lng1: float | Decimal,
    lat2: float | Decimal,
    lng2: float | Decimal,
) -> float:
    """Distancia haversine entre dos puntos lat/lng en metros."""
    lat1f, lng1f, lat2f, lng2f = (float(x) for x in (lat1, lng1, lat2, lng2))
    phi1 = math.radians(lat1f)
    phi2 = math.radians(lat2f)
    dphi = math.radians(lat2f - lat1f)
    dlambda = math.radians(lng2f - lng1f)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))
