"""
Port outbound para lectura y escritura de las capas de configuración.

Opera exclusivamente sobre archivos ``~/.inaki/config/*.yaml``.
NO contacta al daemon, NO recarga ``infrastructure/config.py`` en runtime.
Los cambios toman efecto al próximo reinicio del daemon.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable


class LayerName(str, Enum):
    """Identifica cada una de las 4 capas de configuración."""

    GLOBAL = "global"
    GLOBAL_SECRETS = "global.secrets"
    AGENT = "agent"
    AGENT_SECRETS = "agent.secrets"


@runtime_checkable
class IConfigRepository(Protocol):
    """
    Abstracción de R/W de las capas YAML de ``~/.inaki/config/``.

    Cada método trabaja sobre UNA capa a la vez. El merge de 4 capas
    es responsabilidad de los use cases (``get_effective_config``).
    """

    def read_layer(self, layer: LayerName, agent_id: str | None = None) -> dict:
        """
        Lee la capa indicada y devuelve su contenido como dict.

        Si el archivo no existe devuelve ``{}`` sin error.

        Args:
            layer: Capa a leer.
            agent_id: Requerido para ``LayerName.AGENT`` y ``LayerName.AGENT_SECRETS``.
        """
        ...

    def write_layer(self, layer: LayerName, data: dict, agent_id: str | None = None) -> None:
        """
        Escribe ``data`` en la capa indicada preservando comentarios y orden.

        Si el archivo no existe lo crea (con header comment apropiado).
        Solo escribe la capa indicada — NUNCA toca otras capas.

        Args:
            layer: Capa de destino.
            data: Contenido completo a escribir (CommentedMap o dict plano).
            agent_id: Requerido para ``LayerName.AGENT`` y ``LayerName.AGENT_SECRETS``.
        """
        ...

    def list_agents(self) -> list[str]:
        """
        Enumera los ids de agentes disponibles en ``agents_dir``.

        Retorna una lista ordenada de ids (stems de ``{id}.yaml``),
        excluyendo ``*.secrets.yaml`` y ``*.example.yaml``.
        Lista vacía si no existe ningún agente.
        """
        ...

    def layer_exists(self, layer: LayerName, agent_id: str | None = None) -> bool:
        """
        Retorna ``True`` si el archivo de la capa existe en disco.

        Args:
            layer: Capa a verificar.
            agent_id: Requerido para capas de agente.
        """
        ...

    def delete_layer(self, layer: LayerName, agent_id: str | None = None) -> None:
        """
        Elimina el archivo de la capa indicada si existe.

        Si el archivo no existe es no-op (idempotente).
        Solo está disponible para capas de agente.

        Args:
            layer: Capa a eliminar.
            agent_id: Requerido para ``LayerName.AGENT`` y ``LayerName.AGENT_SECRETS``.
        """
        ...

    def render_yaml(self, data: dict) -> str:
        """
        Serializa ``data`` a string YAML sin escribirlo a disco.

        Útil para generar el diff preview antes de confirmar un guardado.
        """
        ...
