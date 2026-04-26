"""Tests unitarios para UpdateAgentLayerUseCase + lógica de tri-estado."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config.update_agent_layer import (
    CampoTriestado,
    TristadoValor,
    UpdateAgentLayerUseCase,
)


@pytest.fixture()
def repo() -> MagicMock:
    repo = MagicMock(spec=IConfigRepository)
    repo.read_layer.return_value = {}
    return repo


# ---------------------------------------------------------------------------
# Routing de capa
# ---------------------------------------------------------------------------


def test_escribe_en_capa_agent_por_defecto(repo: MagicMock) -> None:
    uc = UpdateAgentLayerUseCase(repo)
    uc.execute("dev", {})

    layer_escrita = repo.write_layer.call_args[0][0]
    assert layer_escrita == LayerName.AGENT


def test_escribe_en_capa_agent_secrets(repo: MagicMock) -> None:
    uc = UpdateAgentLayerUseCase(repo)
    uc.execute("dev", {}, layer=LayerName.AGENT_SECRETS)

    layer_escrita = repo.write_layer.call_args[0][0]
    assert layer_escrita == LayerName.AGENT_SECRETS


def test_capa_global_lanza_error(repo: MagicMock) -> None:
    uc = UpdateAgentLayerUseCase(repo)
    with pytest.raises(ValueError, match="solo acepta capas de agente"):
        uc.execute("dev", {}, layer=LayerName.GLOBAL)


def test_pasa_agent_id_al_repo(repo: MagicMock) -> None:
    uc = UpdateAgentLayerUseCase(repo)
    uc.execute("mi-agente", {"name": "Nuevo"})

    _, kwargs = repo.write_layer.call_args
    positional = repo.write_layer.call_args[0]
    # agent_id se pasa como keyword
    assert repo.write_layer.call_args.kwargs.get("agent_id") == "mi-agente" or \
           (len(positional) >= 3 and positional[2] == "mi-agente")


# ---------------------------------------------------------------------------
# Tri-estado memory.llm.*
# ---------------------------------------------------------------------------


def test_tristado_inherit_elimina_clave(repo: MagicMock) -> None:
    """INHERIT → la clave debe estar AUSENTE del YAML resultante."""
    repo.read_layer.return_value = {
        "memory": {"llm": {"model": "valor-viejo"}}
    }
    uc = UpdateAgentLayerUseCase(repo)
    uc.execute(
        "dev",
        {"memory": {"llm": {"model": CampoTriestado(TristadoValor.INHERIT)}}},
    )

    datos = repo.write_layer.call_args[0][1]
    assert "model" not in datos.get("memory", {}).get("llm", {})


def test_tristado_override_valor_escribe_valor(repo: MagicMock) -> None:
    """OVERRIDE_VALOR → la clave tiene el valor explícito."""
    uc = UpdateAgentLayerUseCase(repo)
    uc.execute(
        "dev",
        {"memory": {"llm": {"model": CampoTriestado(TristadoValor.OVERRIDE_VALOR, "claude-haiku")}}},
    )

    datos = repo.write_layer.call_args[0][1]
    assert datos["memory"]["llm"]["model"] == "claude-haiku"


def test_tristado_override_null_escribe_null(repo: MagicMock) -> None:
    """OVERRIDE_NULL → la clave está presente con valor None (null explícito)."""
    uc = UpdateAgentLayerUseCase(repo)
    uc.execute(
        "dev",
        {"memory": {"llm": {"reasoning_effort": CampoTriestado(TristadoValor.OVERRIDE_NULL)}}},
    )

    datos = repo.write_layer.call_args[0][1]
    llm_override = datos["memory"]["llm"]
    assert "reasoning_effort" in llm_override
    assert llm_override["reasoning_effort"] is None


def test_tristado_inherit_no_elimina_otros_campos(repo: MagicMock) -> None:
    """INHERIT en un campo no afecta a los otros campos del mismo sub-dict."""
    repo.read_layer.return_value = {
        "memory": {"llm": {"model": "viejo", "temperature": 0.5}}
    }
    uc = UpdateAgentLayerUseCase(repo)
    uc.execute(
        "dev",
        {"memory": {"llm": {"model": CampoTriestado(TristadoValor.INHERIT)}}},
    )

    datos = repo.write_layer.call_args[0][1]
    llm = datos["memory"]["llm"]
    assert "model" not in llm
    assert llm.get("temperature") == 0.5
