"""Generador de contraseñas temporales — port de cognipilot-remote/lib/password.ts.

12 chars, alfabeto sin caracteres ambiguos (0/O, 1/l/I).
Usa secrets para entropía criptográfica.
"""
from __future__ import annotations

import secrets

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_temp_password(length: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
