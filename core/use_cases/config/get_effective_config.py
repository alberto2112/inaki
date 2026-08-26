"""
GetEffectiveConfigUseCase — config mergeada de las capas con metadata de origen.

El merge y el rastreo de procedencia los hace el motor único del dominio
(``core/domain/config_merge``): este use case solo decide QUÉ capas se leen y
en qué orden. Antes tenía su propia copia de ``_deep_merge`` y su propio
recorrido de orígenes, que podían divergir del carril de carga sin que nada
avisara.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.domain.config_merge import Capa, merge_capas

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


@dataclass(frozen=True)
class OrigenCampo:
    """Metadata de origen de un valor en la config mergeada."""

    capa: str
    """Nombre de la capa donde se definió el valor: 'global', 'agent'."""


@dataclass(frozen=True)
class ConfigEfectiva:
    """Resultado del merge de capas para un agente dado."""

    datos: dict[str, Any]
    """Config mergeada completa (lo que vería el runtime)."""

    origenes: dict[str, OrigenCampo]
    """Mapa de ruta-de-campo → origen. Ej: ``'llm.model'`` → OrigenCampo(capa='agent')``."""


class GetEffectiveConfigUseCase:
    """
    Devuelve la config efectiva mergeada para un agente (o solo global si ``agent_id=None``).

    Construye:
    - ``datos``: dict mergeado idéntico al que usa el runtime.
    - ``origenes``: mapa de cada ruta de campo a la capa donde fue definida.

    Usa ``IConfigRepository.read_layer`` para leer cada capa individualmente y
    las mergea en el mismo orden que el runtime: ``global.yaml`` es la base y
    ``agents/{id}.yaml`` completa o pisa.
    """

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(self, agent_id: str | None = None) -> ConfigEfectiva:
        """
        Retorna la config efectiva mergeada.

        Args:
            agent_id: Id del agente. ``None`` → solo la capa global.
        """
        from core.ports.config_repository import LayerName

        a_leer: list[tuple[LayerName, str | None, str]] = [(LayerName.GLOBAL, None, "global")]
        if agent_id is not None:
            a_leer.append((LayerName.AGENT, agent_id, "agent"))

        capas = [
            Capa(nombre=nombre, datos=self._repo.read_layer(layer, agent_id=aid))
            for layer, aid, nombre in a_leer
        ]
        resultado = merge_capas(capas)

        return ConfigEfectiva(
            datos=resultado.datos,
            origenes={ruta: OrigenCampo(capa=capa) for ruta, capa in resultado.procedencia.items()},
        )
