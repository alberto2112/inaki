"""
GetEffectiveConfigUseCase — config mergeada de 4 capas con metadata de origen.

EXCEPCIÓN ARQUITECTURAL DOCUMENTADA:
Este use case importa de ``infrastructure.config`` (``load_global_config``,
``load_agent_config``) para aprovechar la lógica de merge y validación ya
definida allí. Es el único use case en ``core/`` con esta licencia.
Razón: la lógica de merge ya es correcta, testeada y coherente con el runtime;
duplicarla en core sería deuda de mantenimiento. Ver design.md §Architecture Decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


@dataclass(frozen=True)
class OrigenCampo:
    """Metadata de origen de un valor en la config mergeada."""

    capa: str
    """Nombre de la capa donde se definió el valor: 'global', 'global.secrets', 'agent', 'agent.secrets'."""  # noqa: E501


@dataclass(frozen=True)
class ConfigEfectiva:
    """Resultado del merge de 4 capas para un agente dado."""

    datos: dict[str, Any]
    """Config mergeada completa (lo que vería el runtime)."""

    origenes: dict[str, OrigenCampo]
    """Mapa de ruta-de-campo → origen. Ej: ``'llm.model'`` → OrigenCampo(capa='agent')``."""


def _flatten_origenes(
    dato: Any,
    prefijo: str,
    capa: str,
    resultado: dict[str, OrigenCampo],
) -> None:
    """Recorre recursivamente ``dato`` y registra el origen de cada clave hoja."""
    if isinstance(dato, dict):
        for k, v in dato.items():
            clave = f"{prefijo}.{k}" if prefijo else k
            _flatten_origenes(v, clave, capa, resultado)
    else:
        # Es un valor hoja — registrar si la clave aún no tiene origen asignado
        # (las capas se procesan en orden creciente de prioridad, así que
        # la última en escribir es la de mayor prioridad)
        resultado[prefijo] = OrigenCampo(capa=capa)


def _merge_origenes(base: dict, override: dict, prefijo: str, resultado: dict) -> None:
    """Merge de dos capas marcando el origen de cada clave."""
    for k, v in override.items():
        clave = f"{prefijo}.{k}" if prefijo else k
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_origenes(base.get(k, {}), v, clave, resultado)
        else:
            _flatten_origenes(v, clave, "override", resultado)


class GetEffectiveConfigUseCase:
    """
    Devuelve la config efectiva mergeada para un agente (o solo global si ``agent_id=None``).

    Construye:
    - ``datos``: dict mergeado idéntico al que usa el runtime.
    - ``origenes``: mapa de cada ruta de campo a la capa donde fue definida.

    Usa ``IConfigRepository.read_layer`` para leer cada capa individualmente,
    luego aplica el mismo orden de merge que ``infrastructure.config``:
    global → global.secrets → agent → agent.secrets.
    """

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(self, agent_id: str | None = None) -> ConfigEfectiva:
        """
        Retorna la config efectiva mergeada.

        Args:
            agent_id: Id del agente. ``None`` → solo capas global.
        """
        from core.ports.config_repository import LayerName

        capas_y_nombres: list[tuple[LayerName, str | None, str]] = [
            (LayerName.GLOBAL, None, "global"),
            (LayerName.GLOBAL_SECRETS, None, "global.secrets"),
        ]
        if agent_id is not None:
            capas_y_nombres += [
                (LayerName.AGENT, agent_id, "agent"),
                (LayerName.AGENT_SECRETS, agent_id, "agent.secrets"),
            ]

        merged: dict[str, Any] = {}
        origenes: dict[str, OrigenCampo] = {}

        for layer, aid, nombre_capa in capas_y_nombres:
            capa_data = self._repo.read_layer(layer, agent_id=aid)
            if not capa_data:
                continue
            # Registrar orígenes: los campos de esta capa pisan a los anteriores
            for k, v in capa_data.items():
                clave_base = k
                _flatten_origenes(v, clave_base, nombre_capa, origenes)
            merged = _deep_merge(merged, capa_data)

        return ConfigEfectiva(datos=merged, origenes=origenes)


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge recursivo campo a campo. Override tiene prioridad."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
