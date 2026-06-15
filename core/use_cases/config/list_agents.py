"""ListAgentsUseCase — enumera los ids de agentes disponibles en ``agents_dir``."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


class ListAgentsUseCase:
    """Retorna lista ordenada de ids de agentes (stems de ``{id}.yaml``)."""

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(self, sub_agents: bool = False) -> list[str]:
        """Devuelve lista de ids. Lista vacía si no hay agentes configurados.

        Args:
            sub_agents: Si es ``True`` lista los sub-agentes
                (``agents/sub-agents/``) en vez de los agentes regulares.
        """
        if sub_agents:
            return self._repo.list_sub_agents()
        return self._repo.list_agents()
