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


def test_no_toca_secrets_al_eliminar_agente(repo: MagicMock) -> None:
    """execute() NO elimina agents/{id}.secrets.yaml."""
    uc = DeleteAgentUseCase(repo)
    uc.execute("dev")

    capas_eliminadas = [call[0][0] for call in repo.delete_layer.call_args_list]
    assert LayerName.AGENT_SECRETS not in capas_eliminadas


def test_agente_inexistente_lanza_error(repo: MagicMock) -> None:
    """Si el agente no existe, lanza AgentNotFoundError sin llamar a delete_layer."""
    repo.layer_exists.return_value = False

    uc = DeleteAgentUseCase(repo)
    with pytest.raises(AgentNotFoundError):
        uc.execute("inexistente")

    repo.delete_layer.assert_not_called()


def test_execute_secrets_elimina_si_existe(repo: MagicMock) -> None:
    """execute_secrets() llama a delete_layer(AGENT_SECRETS) si el archivo existe."""
    repo.layer_exists.side_effect = lambda layer, agent_id=None: layer == LayerName.AGENT_SECRETS

    uc = DeleteAgentUseCase(repo)
    uc.execute_secrets("dev")

    repo.delete_layer.assert_called_once_with(LayerName.AGENT_SECRETS, agent_id="dev")


def test_execute_secrets_no_op_si_no_existe(repo: MagicMock) -> None:
    """execute_secrets() es no-op si el archivo de secrets no existe."""
    repo.layer_exists.return_value = False

    uc = DeleteAgentUseCase(repo)
    uc.execute_secrets("dev")

    repo.delete_layer.assert_not_called()


def test_elimina_subagente_en_capa_sub_agent(repo: MagicMock) -> None:
    """Con layer=SUB_AGENT elimina la capa de sub-agente."""
    uc = DeleteAgentUseCase(repo)
    uc.execute("researcher", layer=LayerName.SUB_AGENT)

    repo.delete_layer.assert_called_once_with(LayerName.SUB_AGENT, agent_id="researcher")


def test_execute_secrets_subagente(repo: MagicMock) -> None:
    """execute_secrets con SUB_AGENT_SECRETS elimina la capa de secrets del sub-agente."""
    repo.layer_exists.side_effect = lambda layer, agent_id=None: (
        layer == LayerName.SUB_AGENT_SECRETS
    )

    uc = DeleteAgentUseCase(repo)
    uc.execute_secrets("researcher", secrets_layer=LayerName.SUB_AGENT_SECRETS)

    repo.delete_layer.assert_called_once_with(LayerName.SUB_AGENT_SECRETS, agent_id="researcher")


def test_execute_layer_invalida_lanza_error(repo: MagicMock) -> None:
    """Una capa que no sea AGENT/SUB_AGENT en execute lanza ValueError."""
    uc = DeleteAgentUseCase(repo)
    with pytest.raises(ValueError, match="AGENT o SUB_AGENT"):
        uc.execute("x", layer=LayerName.GLOBAL)

    repo.delete_layer.assert_not_called()


def test_execute_secrets_layer_invalida_lanza_error(repo: MagicMock) -> None:
    """Una capa que no sea de secrets de agente en execute_secrets lanza ValueError."""
    uc = DeleteAgentUseCase(repo)
    with pytest.raises(ValueError, match="AGENT_SECRETS o "):
        uc.execute_secrets("x", secrets_layer=LayerName.AGENT)

    repo.delete_layer.assert_not_called()
