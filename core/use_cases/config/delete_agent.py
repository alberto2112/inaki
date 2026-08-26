"""
DeleteAgentUseCase — elimina la capa YAML de un agente.

``execute(agent_id)`` elimina ``agents/{id}.yaml`` (o el equivalente de
sub-agente), que desde la erradicación de los ``*.secrets.yaml`` es el único
archivo del agente: se lleva también sus credenciales. La TUI confirma con el
usuario antes de llamar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.domain.errors import AgentNotFoundError
from core.ports.config_repository import LayerName

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


class DeleteAgentUseCase:
    """Elimina el archivo YAML de un agente."""

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(self, agent_id: str, layer: LayerName = LayerName.AGENT) -> None:
        """
        Elimina el YAML del agente (``agents/{id}.yaml``), credenciales incluidas.

        Args:
            agent_id: Id del agente a eliminar.
            layer: Capa a eliminar. ``AGENT`` (default) para un agente regular;
                ``SUB_AGENT`` para un sub-agente.

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
