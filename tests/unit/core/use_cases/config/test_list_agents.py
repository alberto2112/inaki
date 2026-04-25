"""Tests unitarios para ListAgentsUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.ports.config_repository import IConfigRepository
from core.use_cases.config.list_agents import ListAgentsUseCase


@pytest.fixture()
def repo() -> MagicMock:
    return MagicMock(spec=IConfigRepository)


def test_lista_agentes(repo: MagicMock) -> None:
    """Devuelve la lista que retorna el repo."""
    repo.list_agents.return_value = ["dev", "general", "scheduler"]

    uc = ListAgentsUseCase(repo)
    resultado = uc.execute()

    assert resultado == ["dev", "general", "scheduler"]
    repo.list_agents.assert_called_once()


def test_lista_vacia(repo: MagicMock) -> None:
    """Si no hay agentes, devuelve lista vacía sin error."""
    repo.list_agents.return_value = []

    uc = ListAgentsUseCase(repo)
    assert uc.execute() == []
