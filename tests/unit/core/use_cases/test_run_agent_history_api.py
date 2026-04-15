"""Tests para la API pública de historial de RunAgentUseCase.

Cubre Design §D1:
  - get_history() delega a _history.load(agent_id) y retorna la lista de mensajes.
  - clear_history() delega a _history.clear(agent_id) y propaga excepciones.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.domain.entities.message import Message, Role
from core.use_cases.run_agent import RunAgentUseCase


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_history() -> AsyncMock:
    historia = AsyncMock()
    historia.load.return_value = []
    historia.clear.return_value = None
    return historia


@pytest.fixture
def caso_uso(agent_config, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools) -> RunAgentUseCase:
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
# Tarea 1.1 — get_history
# ---------------------------------------------------------------------------


async def test_get_history_delega_a_history_load(caso_uso: RunAgentUseCase, mock_history: AsyncMock, agent_config) -> None:
    """get_history() debe delegar a _history.load con el agent_id correcto."""
    mensajes_esperados = [Message(role=Role.USER, content="hola")]
    mock_history.load.return_value = mensajes_esperados

    resultado = await caso_uso.get_history()

    mock_history.load.assert_awaited_once_with(agent_config.id)
    assert resultado == mensajes_esperados


async def test_get_history_retorna_lista_vacia_sin_mensajes(caso_uso: RunAgentUseCase, mock_history: AsyncMock) -> None:
    """get_history() retorna lista vacía cuando no hay historial."""
    mock_history.load.return_value = []

    resultado = await caso_uso.get_history()

    assert resultado == []


# ---------------------------------------------------------------------------
# Tarea 1.3 — clear_history
# ---------------------------------------------------------------------------


async def test_clear_history_delega_a_history_clear(caso_uso: RunAgentUseCase, mock_history: AsyncMock, agent_config) -> None:
    """clear_history() debe delegar a _history.clear con el agent_id correcto."""
    await caso_uso.clear_history()

    mock_history.clear.assert_awaited_once_with(agent_config.id)


async def test_clear_history_propaga_excepciones(caso_uso: RunAgentUseCase, mock_history: AsyncMock) -> None:
    """clear_history() propaga cualquier excepción que levante el repositorio."""
    mock_history.clear.side_effect = RuntimeError("error de almacenamiento")

    with pytest.raises(RuntimeError, match="error de almacenamiento"):
        await caso_uso.clear_history()
