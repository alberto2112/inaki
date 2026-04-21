"""Tests para la API pública de info de agente de RunAgentUseCase.

Cubre Fix 1 — Judgment Day:
  - get_agent_info() retorna AgentInfoDTO con id, name, description del agente.
  - Evita acceso directo a _cfg desde adapters externos.
"""

from __future__ import annotations

import pytest

from core.use_cases.run_agent import RunAgentUseCase


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def caso_uso(
    agent_config, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
) -> RunAgentUseCase:
    """Instancia de RunAgentUseCase con todos los colaboradores mockeados."""
    return RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )


# ---------------------------------------------------------------------------
# Fix 1 — get_agent_info()
# ---------------------------------------------------------------------------


def test_get_agent_info_retorna_id(caso_uso: RunAgentUseCase, agent_config) -> None:
    """get_agent_info() expone el id del agente sin acceso a _cfg."""
    info = caso_uso.get_agent_info()
    assert info.id == agent_config.id


def test_get_agent_info_retorna_name(caso_uso: RunAgentUseCase, agent_config) -> None:
    """get_agent_info() expone el nombre del agente."""
    info = caso_uso.get_agent_info()
    assert info.name == agent_config.name


def test_get_agent_info_retorna_description(caso_uso: RunAgentUseCase, agent_config) -> None:
    """get_agent_info() expone la descripción del agente."""
    info = caso_uso.get_agent_info()
    assert info.description == agent_config.description


def test_get_agent_info_retorna_tipo_correcto(caso_uso: RunAgentUseCase) -> None:
    """get_agent_info() retorna un objeto con los tres campos esperados."""
    info = caso_uso.get_agent_info()
    assert hasattr(info, "id")
    assert hasattr(info, "name")
    assert hasattr(info, "description")
