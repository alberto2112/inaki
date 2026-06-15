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

    def execute(self, agent_id: str, layer: LayerName = LayerName.AGENT) -> None:
        """
        Elimina el YAML principal del agente (``agents/{id}.yaml``).

        No toca el archivo de secrets.

        Args:
            agent_id: Id del agente a eliminar.
            layer: Capa principal a eliminar. ``AGENT`` (default) para un agente
                regular; ``SUB_AGENT`` para un sub-agente.

        Raises:
            ValueError: Si ``layer`` no es ``AGENT`` ni ``SUB_AGENT``.
            AgentNotFoundError: Si el archivo del agente no existe.
        """
        if layer not in (LayerName.AGENT, LayerName.SUB_AGENT):
            raise ValueError(
                f"DeleteAgentUseCase solo acepta AGENT o SUB_AGENT, recibió: {layer!r}"
            )
        if not self._repo.layer_exists(layer, agent_id=agent_id):
            raise AgentNotFoundError(f"Agente '{agent_id}' no encontrado.")
        self._repo.delete_layer(layer, agent_id=agent_id)

    def execute_secrets(
        self, agent_id: str, secrets_layer: LayerName = LayerName.AGENT_SECRETS
    ) -> None:
        """
        Elimina el archivo de secrets del agente si existe.

        Es no-op si el archivo no existe (idempotente).

        Args:
            agent_id: Id del agente cuyo archivo de secrets se elimina.
            secrets_layer: Capa de secrets a eliminar. ``AGENT_SECRETS`` (default)
                para un agente regular; ``SUB_AGENT_SECRETS`` para un sub-agente.

        Raises:
            ValueError: Si ``secrets_layer`` no es una capa de secrets de agente.
        """
        if secrets_layer not in (LayerName.AGENT_SECRETS, LayerName.SUB_AGENT_SECRETS):
            raise ValueError(
                "DeleteAgentUseCase.execute_secrets solo acepta AGENT_SECRETS o "
                f"SUB_AGENT_SECRETS, recibió: {secrets_layer!r}"
            )
        if self._repo.layer_exists(secrets_layer, agent_id=agent_id):
            self._repo.delete_layer(secrets_layer, agent_id=agent_id)
