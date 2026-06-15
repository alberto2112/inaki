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


def test_lista_subagentes(repo: MagicMock) -> None:
    """Con sub_agents=True usa list_sub_agents (no list_agents)."""
    repo.list_sub_agents.return_value = ["researcher", "summarizer"]

    uc = ListAgentsUseCase(repo)
    resultado = uc.execute(sub_agents=True)

    assert resultado == ["researcher", "summarizer"]
    repo.list_sub_agents.assert_called_once()
    repo.list_agents.assert_not_called()


def test_default_no_toca_subagentes(repo: MagicMock) -> None:
    """Sin sub_agents usa list_agents y no llama a list_sub_agents."""
    repo.list_agents.return_value = ["dev"]

    uc = ListAgentsUseCase(repo)
    assert uc.execute() == ["dev"]
    repo.list_sub_agents.assert_not_called()
