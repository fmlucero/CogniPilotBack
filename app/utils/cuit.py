"""Validación liviana de CUIT — port directo de cognipilot-remote/lib/cuit.ts.

Decisión heredada: NO validar dígito verificador módulo 11, porque rechazaba
CUITs reales que el usuario quería registrar. Unicidad + formato bastan.
"""
from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D")


def normalize_cuit(value: str) -> str:
    return _DIGITS_RE.sub("", value)[:11]


def format_cuit(value: str) -> str:
    n = normalize_cuit(value)
    if len(n) != 11:
        return value
    return f"{n[:2]}-{n[2:10]}-{n[10:]}"


def is_valid_cuit(value: str) -> bool:
    return len(normalize_cuit(value)) == 11
