"""Tests unitarios para CreateAgentUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.domain.errors import AgentYaExisteError
from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config.create_agent import CreateAgentUseCase


@pytest.fixture()
def repo() -> MagicMock:
    repo = MagicMock(spec=IConfigRepository)
    repo.layer_exists.return_value = False
    return repo


def test_crea_agente_nuevo(repo: MagicMock) -> None:
    """Si el id no existe, crea agents/{id}.yaml con los datos básicos."""
    uc = CreateAgentUseCase(repo)
    uc.execute("nuevo", nombre="Nuevo Agente")

    repo.write_layer.assert_called_once()
    layer, datos, *resto = repo.write_layer.call_args[0]
    assert layer == LayerName.AGENT
    assert datos["id"] == "nuevo"
    assert datos["name"] == "Nuevo Agente"


def test_agent_ya_existe_lanza_error(repo: MagicMock) -> None:
    """Si el id ya existe, lanza AgentYaExisteError sin escribir ningún archivo."""
    repo.layer_exists.return_value = True

    uc = CreateAgentUseCase(repo)
    with pytest.raises(AgentYaExisteError) as exc_info:
        uc.execute("existente", nombre="X")

    assert exc_info.value.agent_id == "existente"
    repo.write_layer.assert_not_called()


def test_no_crea_secrets_automaticamente(repo: MagicMock) -> None:
    """No se crea agents/{id}.secrets.yaml automáticamente."""
    uc = CreateAgentUseCase(repo)
    uc.execute("nuevo", nombre="Nuevo")

    capas_escritas = [call[0][0] for call in repo.write_layer.call_args_list]
    assert LayerName.AGENT_SECRETS not in capas_escritas


def test_template_extra_se_mezcla(repo: MagicMock) -> None:
    """Los campos del template_extra se incluyen en el YAML generado."""
    uc = CreateAgentUseCase(repo)
    uc.execute(
        "dev",
        nombre="Dev",
        template_extra={"llm": {"model": "claude-haiku"}},
    )

    datos = repo.write_layer.call_args[0][1]
    assert datos.get("llm", {}).get("model") == "claude-haiku"


def test_system_prompt_custom(repo: MagicMock) -> None:
    """El system_prompt custom se usa en vez del template base."""
    uc = CreateAgentUseCase(repo)
    uc.execute("dev", nombre="Dev", system_prompt="Mi prompt personalizado.")

    datos = repo.write_layer.call_args[0][1]
    assert datos["system_prompt"] == "Mi prompt personalizado."


def test_layer_exists_verifica_capa_agent(repo: MagicMock) -> None:
    """Verifica la existencia en la capa AGENT (no en secrets)."""
    uc = CreateAgentUseCase(repo)
    uc.execute("nuevo", nombre="Nuevo")

    layer_chequeada = repo.layer_exists.call_args[0][0]
    assert layer_chequeada == LayerName.AGENT
