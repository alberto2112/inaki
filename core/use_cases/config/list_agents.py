"""ListAgentsUseCase — enumera los ids de agentes disponibles en ``agents_dir``."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository


class ListAgentsUseCase:
    """Retorna lista ordenada de ids de agentes (stems de ``{id}.yaml``)."""

    def __init__(self, repo: "IConfigRepository") -> None:
        self._repo = repo

    def execute(self) -> list[str]:
        """Devuelve lista de ids. Lista vacía si no hay agentes configurados."""
        return self._repo.list_agents()
