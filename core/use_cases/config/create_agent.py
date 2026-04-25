"""
CreateAgentUseCase — crea un nuevo agente desde una plantilla mínima.

Valida que el id sea único. Si ya existe, lanza ``AgentYaExisteError``
sin modificar ningún archivo.

El archivo de secrets NO se crea automáticamente — solo se crea
``agents/{id}.yaml`` con los valores del template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.domain.errors import AgentYaExisteError
from core.ports.config_repository import LayerName

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository

# Plantilla mínima para un agente nuevo.
# El caller puede pasar ``template_extra`` para enriquecer los campos.
_TEMPLATE_BASE: dict[str, Any] = {
    "id": "",
    "name": "",
    "description": "",
    "system_prompt": "Sos un asistente de IA.",
}


class CreateAgentUseCase:
    """
    Crea ``agents/{id}.yaml`` con una plantilla mínima.

    No crea ``agents/{id}.secrets.yaml`` — si el agente necesita secrets
    (token de Telegram, etc.) el usuario los agrega después vía la TUI.
    """

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(
        self,
        agent_id: str,
        nombre: str,
        descripcion: str = "",
        system_prompt: str = "",
        template_extra: dict[str, Any] | None = None,
    ) -> None:
        """
        Crea el agente si el id es único.

        Args:
            agent_id: Id único del agente (slug, sin espacios).
            nombre: Nombre legible del agente.
            descripcion: Descripción breve (opcional).
            system_prompt: System prompt inicial (opcional).
            template_extra: Campos adicionales a mezclar en el YAML generado.

        Raises:
            AgentYaExisteError: Si ``agents/{agent_id}.yaml`` ya existe.
        """
        if self._repo.layer_exists(LayerName.AGENT, agent_id=agent_id):
            raise AgentYaExisteError(agent_id)

        datos: dict[str, Any] = {
            **_TEMPLATE_BASE,
            "id": agent_id,
            "name": nombre,
            "description": descripcion,
            "system_prompt": system_prompt or _TEMPLATE_BASE["system_prompt"],
        }
        if template_extra:
            datos.update(template_extra)

        self._repo.write_layer(LayerName.AGENT, datos, agent_id=agent_id)
