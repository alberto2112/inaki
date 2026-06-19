"""
UpdateAgentLayerUseCase — escribe cambios en la capa de agente o agente.secrets.

Gestiona el tri-estado de ``memory.llm.*`` (Inherit / Override / Override-to-null):
- ``TristadoValor.INHERIT`` → elimina la clave del YAML (ausente = heredar de global).
- ``TristadoValor.OVERRIDE_VALOR`` → escribe el valor explícito.
- ``TristadoValor.OVERRIDE_NULL`` → escribe la clave con valor ``null`` explícito.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.ports.config_repository import LayerName
from core.use_cases.config._merge import (
    CampoTriestado,
    TristadoValor,
    deep_merge_con_eliminaciones,
    resolver_tristados,
)

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository

# Re-exportados desde ``_merge`` para no romper imports existentes
# (``setup_tui/screens/agent_detail_page.py`` y los tests los toman de acá).
__all__ = ["CampoTriestado", "TristadoValor", "UpdateAgentLayerUseCase"]


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
            layer: Solo capas de agente son válidas: ``AGENT``, ``AGENT_SECRETS``,
                ``SUB_AGENT`` o ``SUB_AGENT_SECRETS``.

        Raises:
            ValueError: Si se pasa una capa global.
        """
        if layer not in (
            LayerName.AGENT,
            LayerName.AGENT_SECRETS,
            LayerName.SUB_AGENT,
            LayerName.SUB_AGENT_SECRETS,
        ):
            raise ValueError(
                f"UpdateAgentLayerUseCase solo acepta capas de agente, recibió: {layer!r}"
            )

        datos_actuales = self._repo.read_layer(layer, agent_id=agent_id)
        datos_resueltos = resolver_tristados(cambios)
        datos_nuevos = deep_merge_con_eliminaciones(datos_actuales, datos_resueltos)
        self._repo.write_layer(layer, datos_nuevos, agent_id=agent_id)
