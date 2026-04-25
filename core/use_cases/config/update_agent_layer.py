"""
UpdateAgentLayerUseCase — escribe cambios en la capa de agente o agente.secrets.

Gestiona el tri-estado de ``memory.llm.*`` (Inherit / Override / Override-to-null):
- ``TristadoValor.INHERIT`` → elimina la clave del YAML (ausente = heredar de global).
- ``TristadoValor.OVERRIDE_VALOR`` → escribe el valor explícito.
- ``TristadoValor.OVERRIDE_NULL`` → escribe la clave con valor ``null`` explícito.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from core.ports.config_repository import LayerName

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository

# Sentinel que diferencia "no tocar este campo" de "borrar este campo".
_SENTINEL_ELIMINAR = object()


class TristadoValor(str, Enum):
    """
    Tri-estado para campos que distinguen ausente vs null vs valor explícito.

    Aplica principalmente a ``memory.llm.*`` en la config de agentes:
    - ``INHERIT`` → campo ausente del YAML (hereda del LLM base del agente).
    - ``OVERRIDE_VALOR`` → campo presente con valor explícito.
    - ``OVERRIDE_NULL`` → campo presente con valor ``null`` (pisa con None explícito).
    """

    INHERIT = "inherit"
    OVERRIDE_VALOR = "valor"
    OVERRIDE_NULL = "null"


class CampoTriestado:
    """Envuelve un valor con su modo tri-estado."""

    def __init__(self, modo: TristadoValor, valor: Any = None) -> None:
        self.modo = modo
        self.valor = valor


class UpdateAgentLayerUseCase:
    """
    Actualiza campos en la capa de agente indicada.

    Soporta tri-estado en ``memory.llm.*``: si el caller pasa un
    ``CampoTriestado`` para un sub-campo de ``memory.llm``, el use case
    lo resuelve correctamente (INHERIT = eliminar clave; OVERRIDE_NULL =
    escribir ``null`` explícito).
    """

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(
        self,
        agent_id: str,
        cambios: dict[str, Any],
        layer: LayerName = LayerName.AGENT,
    ) -> None:
        """
        Aplica ``cambios`` en la capa ``layer`` del agente ``agent_id``.

        Args:
            agent_id: Id del agente cuya capa se modifica.
            cambios: Dict con los campos a actualizar.
                     Los valores pueden ser ``CampoTriestado`` para campos
                     bajo ``memory.llm.*`` que usan tri-estado.
            layer: Solo ``AGENT`` o ``AGENT_SECRETS`` son válidos.

        Raises:
            ValueError: Si se pasa una capa global.
        """
        if layer not in (LayerName.AGENT, LayerName.AGENT_SECRETS):
            raise ValueError(
                f"UpdateAgentLayerUseCase solo acepta capas de agente, recibió: {layer!r}"
            )

        datos_actuales = self._repo.read_layer(layer, agent_id=agent_id)
        datos_resueltos = _resolver_tristados(cambios)
        datos_nuevos = _deep_merge_con_eliminaciones(datos_actuales, datos_resueltos)
        self._repo.write_layer(layer, datos_nuevos, agent_id=agent_id)


def _resolver_tristados(cambios: dict[str, Any]) -> dict[str, Any]:
    """
    Recorre ``cambios`` y reemplaza ``CampoTriestado`` por su valor efectivo
    o por ``_SENTINEL_ELIMINAR`` si el modo es INHERIT.
    """
    resultado: dict[str, Any] = {}
    for k, v in cambios.items():
        if isinstance(v, CampoTriestado):
            if v.modo == TristadoValor.INHERIT:
                resultado[k] = _SENTINEL_ELIMINAR
            elif v.modo == TristadoValor.OVERRIDE_NULL:
                resultado[k] = None
            else:
                resultado[k] = v.valor
        elif isinstance(v, dict):
            resultado[k] = _resolver_tristados(v)
        else:
            resultado[k] = v
    return resultado


def _deep_merge_con_eliminaciones(base: dict, override: dict) -> dict:
    """
    Merge recursivo que respeta el sentinel de eliminación.

    - Si el valor en override es ``_SENTINEL_ELIMINAR`` → elimina la clave de base.
    - Si es dict en ambos → merge recursivo.
    - Caso contrario → override pisa base.
    """
    result = dict(base)
    for key, value in override.items():
        if value is _SENTINEL_ELIMINAR:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_con_eliminaciones(result[key], value)
        else:
            result[key] = value
    return result
