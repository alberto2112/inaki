"""Primitivas de merge de capas de configuración con soporte de eliminación.

Compartido por ``UpdateAgentLayerUseCase`` y ``UpdateGlobalLayerUseCase`` para
que el borrado de claves (vía el tri-estado ``INHERIT``) funcione UNIFORME en
las capas de agente y globales — antes solo el carril de agente lo soportaba.

El tri-estado (``CampoTriestado`` / ``TristadoValor``) distingue tres
intenciones sobre un campo:
- ``INHERIT`` → eliminar la clave del YAML (ausente = heredar de la capa previa).
- ``OVERRIDE_VALOR`` → escribir un valor explícito.
- ``OVERRIDE_NULL`` → escribir la clave con ``null`` explícito.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

# Sentinel que diferencia "no tocar este campo" de "borrar este campo".
SENTINEL_ELIMINAR = object()


class TristadoValor(str, Enum):
    """
    Tri-estado para campos que distinguen ausente vs null vs valor explícito.

    Aplica a ``memory.llm.*`` en la config de agentes y, de forma general, a
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


def deep_merge_con_eliminaciones(base: dict, override: dict) -> dict:
    """
    Merge recursivo que respeta el sentinel de eliminación.

    - Si el valor en override es ``SENTINEL_ELIMINAR`` → elimina la clave de base.
    - Si es dict en ambos → merge recursivo.
    - Caso contrario → override pisa base.
    """
    result = dict(base)
    for key, value in override.items():
        if value is SENTINEL_ELIMINAR:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge_con_eliminaciones(result[key], value)
        else:
            result[key] = value
    return result
