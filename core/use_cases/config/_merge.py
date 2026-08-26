"""Traducción de la intención de la UI a los primitivos del motor de merge.

El merge en sí vive en ``core/domain/config_merge`` — acá solo se traduce lo
que el setup TUI quiere decir sobre un campo a los tres primitivos que el motor
ya entiende:

| Intención de la UI | Primitivo del motor |
|---|---|
| ``INHERIT`` — "que lo herede de la capa previa" | ``SENTINEL_ELIMINAR`` (saca la clave) |
| ``OVERRIDE_NULL`` — "quiero un null explícito" | ``None`` |
| ``OVERRIDE_VALOR`` — "quiero este valor" | el valor |

El tri-estado existe porque en un YAML "ausente" y "null" NO son lo mismo:
ausente hereda, null pisa con nada. La UI necesita poder pedir las dos cosas —
y también borrar, que es cómo se vuelve a "ausente".
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from core.domain.config_merge import SENTINEL_ELIMINAR, deep_merge

__all__ = [
    "SENTINEL_ELIMINAR",
    "CampoTriestado",
    "TristadoValor",
    "deep_merge_con_eliminaciones",
    "resolver_tristados",
]


class TristadoValor(str, Enum):
    """
    Tri-estado para campos que distinguen ausente vs null vs valor explícito.

    Aplica a ``memories.llm.*`` en la config de agentes y, de forma general, a
    cualquier borrado de clave por path (ver ``setup_tui/_cambios.py``):
    - ``INHERIT`` → campo ausente del YAML (hereda de la capa previa).
    - ``OVERRIDE_VALOR`` → campo presente con valor explícito.
    - ``OVERRIDE_NULL`` → campo presente con valor ``null``.
    """

    INHERIT = "inherit"
    OVERRIDE_VALOR = "valor"
    OVERRIDE_NULL = "null"


class CampoTriestado:
    """Envuelve un valor con su modo tri-estado."""

    def __init__(self, modo: TristadoValor, valor: Any = None) -> None:
        self.modo = modo
        self.valor = valor


def resolver_tristados(cambios: dict[str, Any]) -> dict[str, Any]:
    """
    Recorre ``cambios`` y reemplaza ``CampoTriestado`` por su valor efectivo
    o por ``SENTINEL_ELIMINAR`` si el modo es INHERIT.
    """
    resultado: dict[str, Any] = {}
    for k, v in cambios.items():
        if isinstance(v, CampoTriestado):
            if v.modo == TristadoValor.INHERIT:
                resultado[k] = SENTINEL_ELIMINAR
            elif v.modo == TristadoValor.OVERRIDE_NULL:
                resultado[k] = None
            else:
                resultado[k] = v.valor
        elif isinstance(v, dict):
            resultado[k] = resolver_tristados(v)
        else:
            resultado[k] = v
    return resultado


# El carril de edición usa exactamente el mismo merge que el de carga: el
# sentinel de borrado ya es parte de la semántica del motor, no un añadido de
# esta capa. El alias conserva el nombre histórico de los use cases.
deep_merge_con_eliminaciones = deep_merge
