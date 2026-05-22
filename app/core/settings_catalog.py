"""HU-39 — Catálogo de settings editables desde el panel admin.

Los valores se persisten en la tabla `SystemSetting` (key/value/JSONB), pero
la metadata (tipo, label, descripción, default) vive acá para que agregar un
setting nuevo sea sólo extender este dict.

Convención de keys: `<categoria>.<nombre>` snake_case.
"""
from __future__ import annotations

from typing import Any, Literal

SettingType = Literal["int", "str", "bool"]


class SettingMeta:
    __slots__ = ("key", "type", "label", "description", "default", "category", "hot_reload")

    def __init__(
        self,
        key: str,
        type: SettingType,
        label: str,
        description: str,
        default: Any,
        category: str,
        hot_reload: bool = True,
    ) -> None:
        self.key = key
        self.type = type
        self.label = label
        self.description = description
        self.default = default
        self.category = category
        # Si False, el valor solo aplica al próximo restart del back. La UI
        # debe mostrar esto como warning.
        self.hot_reload = hot_reload

    def to_dict(self, current_value: Any) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "default": self.default,
            "category": self.category,
            "hot_reload": self.hot_reload,
            "value": current_value,
        }


# Orden de aparición en la UI = orden de este dict.
SETTINGS_CATALOG: dict[str, SettingMeta] = {
    "umbrales.default_errores_jornada": SettingMeta(
        key="umbrales.default_errores_jornada",
        type="int",
        label="Default de errores por jornada (nuevas empresas)",
        description="Valor que se asigna a Empresa.umbralErroresJornada cuando "
                    "se crea una empresa nueva. No retro-modifica las existentes.",
        default=3,
        category="Umbrales operativos",
    ),
    "nudge.radio_parada_m": SettingMeta(
        key="nudge.radio_parada_m",
        type="int",
        label="Radio del nudge de parada (m)",
        description="Radio en metros para que la app Android dispare el nudge "
                    "informativo cuando el repartidor entra a una parada (HU-09).",
        default=50,
        category="Nudges y proximidad",
    ),
    "retencion.eventos_dias": SettingMeta(
        key="retencion.eventos_dias",
        type="int",
        label="Retención de eventos (días)",
        description="Días que se conservan las filas de EventoApp antes del "
                    "purge programado. Aún no hay job de purge — este valor "
                    "queda registrado para cuando se implemente.",
        default=90,
        category="Retención",
    ),
    "auth.access_token_ttl_min": SettingMeta(
        key="auth.access_token_ttl_min",
        type="int",
        label="TTL access token (min)",
        description="Vida del JWT de acceso. Cambios requieren restart del "
                    "back-api para tomar efecto (settings.access_token_ttl_min "
                    "se lee en startup).",
        default=15,
        category="Autenticación",
        hot_reload=False,
    ),
}


def coerce(value: Any, type_: SettingType) -> Any:
    """Valida y casteea un value JSON al tipo esperado del setting."""
    if type_ == "int":
        if isinstance(value, bool):
            raise ValueError("se esperaba int, llegó bool")
        return int(value)
    if type_ == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError("se esperaba bool")
    if type_ == "str":
        if not isinstance(value, str):
            raise ValueError("se esperaba str")
        return value
    raise ValueError(f"tipo no soportado: {type_}")
