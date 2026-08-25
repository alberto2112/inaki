"""Tests unitarios para DeleteAgentUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.domain.errors import AgentNotFoundError
from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config.delete_agent import DeleteAgentUseCase


@pytest.fixture()
def repo() -> MagicMock:
    repo = MagicMock(spec=IConfigRepository)
    repo.layer_exists.return_value = True
    return repo


def test_elimina_yaml_del_agente(repo: MagicMock) -> None:
    """Llama a delete_layer con la capa AGENT."""
    uc = DeleteAgentUseCase(repo)
    uc.execute("dev")

    repo.delete_layer.assert_called_once_with(LayerName.AGENT, agent_id="dev")


def test_elimina_una_sola_capa_con_las_credenciales(repo: MagicMock) -> None:
    """El agente tiene UN solo archivo: borrarlo se lleva sus credenciales.

    Ya no queda un ``agents/{id}.secrets.yaml`` huérfano que borrar aparte.
    """
    uc = DeleteAgentUseCase(repo)
    uc.execute("dev")

    capas_eliminadas = [call[0][0] for call in repo.delete_layer.call_args_list]
    assert capas_eliminadas == [LayerName.AGENT]


def test_agente_inexistente_lanza_error(repo: MagicMock) -> None:
    """Si el agente no existe, lanza AgentNotFoundError sin llamar a delete_layer."""
    repo.layer_exists.return_value = False

    uc = DeleteAgentUseCase(repo)
    with pytest.raises(AgentNotFoundError):
        uc.execute("inexistente")

    repo.delete_layer.assert_not_called()


def test_verifica_existencia_en_la_capa_que_va_a_borrar(repo: MagicMock) -> None:
    """El chequeo de existencia se hace sobre la capa pedida, no sobre otra."""
    uc = DeleteAgentUseCase(repo)
    uc.execute("dev")

    repo.layer_exists.assert_called_once_with(LayerName.AGENT, agent_id="dev")


def test_elimina_subagente_en_capa_sub_agent(repo: MagicMock) -> None:
    """Con layer=SUB_AGENT elimina la capa de sub-agente."""
    uc = DeleteAgentUseCase(repo)
    uc.execute("researcher", layer=LayerName.SUB_AGENT)

    repo.delete_layer.assert_called_once_with(LayerName.SUB_AGENT, agent_id="researcher")


def test_subagente_inexistente_lanza_error(repo: MagicMock) -> None:
    """El sub-agente inexistente también lanza AgentNotFoundError."""
    repo.layer_exists.return_value = False

    uc = DeleteAgentUseCase(repo)
    with pytest.raises(AgentNotFoundError):
        uc.execute("inexistente", layer=LayerName.SUB_AGENT)

    repo.layer_exists.assert_called_once_with(LayerName.SUB_AGENT, agent_id="inexistente")
    repo.delete_layer.assert_not_called()


def test_execute_layer_invalida_lanza_error(repo: MagicMock) -> None:
    """Una capa que no sea AGENT/SUB_AGENT en execute lanza ValueError."""
    uc = DeleteAgentUseCase(repo)
    with pytest.raises(ValueError, match="AGENT o SUB_AGENT"):
        uc.execute("x", layer=LayerName.GLOBAL)

    repo.delete_layer.assert_not_called()
