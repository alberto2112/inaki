"""
DeleteAgentUseCase — elimina la capa YAML de un agente.

``execute(agent_id)`` — elimina solo ``agents/{id}.yaml``.
``execute_secrets(agent_id)`` — elimina solo ``agents/{id}.secrets.yaml``.

Los secrets NO se tocan en ``execute`` para evitar pérdida accidental.
La TUI confirma con el usuario antes de llamar a ``execute_secrets``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.domain.errors import AgentNotFoundError
from core.ports.config_repository import LayerName

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


class DeleteAgentUseCase:
    """Elimina los archivos YAML de un agente, respetando la separación de secrets."""

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(self, agent_id: str) -> None:
        """
        Elimina ``agents/{agent_id}.yaml``.

        No toca ``agents/{agent_id}.secrets.yaml``.

        Args:
            agent_id: Id del agente a eliminar.

        Raises:
            AgentNotFoundError: Si el archivo del agente no existe.
        """
        if not self._repo.layer_exists(LayerName.AGENT, agent_id=agent_id):
            raise AgentNotFoundError(f"Agente '{agent_id}' no encontrado.")
        self._repo.delete_layer(LayerName.AGENT, agent_id=agent_id)

    def execute_secrets(self, agent_id: str) -> None:
        """
        Elimina ``agents/{agent_id}.secrets.yaml`` si existe.

        Es no-op si el archivo no existe (idempotente).

        Args:
            agent_id: Id del agente cuyo archivo de secrets se elimina.
        """
        if self._repo.layer_exists(LayerName.AGENT_SECRETS, agent_id=agent_id):
            self._repo.delete_layer(LayerName.AGENT_SECRETS, agent_id=agent_id)
