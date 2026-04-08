"""Tests unitarios para RunAgentUseCase — flujo básico."""

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from core.use_cases.run_agent import RunAgentUseCase
from core.domain.entities.message import Message, Role


@pytest.fixture
def use_case(agent_config, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools):
    return RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )


async def test_execute_returns_llm_response(use_case, mock_llm):
    mock_llm.complete.return_value = "Hola, soy Iñaki"
    response = await use_case.execute("Hola")
    assert response == "Hola, soy Iñaki"


async def test_execute_persists_user_and_assistant_messages(use_case, mock_llm, mock_history):
    mock_llm.complete.return_value = "Respuesta"
    await use_case.execute("Hola")

    calls = mock_history.append.call_args_list
    assert len(calls) == 2
    user_msg = calls[0].args[1]
    assistant_msg = calls[1].args[1]
    assert user_msg.role == Role.USER
    assert user_msg.content == "Hola"
    assert assistant_msg.role == Role.ASSISTANT
    assert assistant_msg.content == "Respuesta"


async def test_execute_loads_history_before_calling_llm(use_case, mock_history, mock_llm):
    existing = [Message(role=Role.USER, content="mensaje previo")]
    mock_history.load.return_value = existing
    await use_case.execute("nuevo mensaje")

    mock_history.load.assert_called_once_with("test")
    # El LLM recibe el historial cargado + el nuevo mensaje
    call_args = mock_llm.complete.call_args
    messages_passed = call_args.args[0]
    assert any(m.content == "mensaje previo" for m in messages_passed)
    assert any(m.content == "nuevo mensaje" for m in messages_passed)


async def test_execute_calls_embed_query(use_case, mock_embedder):
    await use_case.execute("test input")
    mock_embedder.embed_query.assert_called_once_with("test input")


async def test_execute_searches_memory_with_embedding(use_case, mock_memory, mock_embedder):
    mock_embedder.embed_query.return_value = [0.5] * 384
    await use_case.execute("test")
    mock_memory.search.assert_called_once_with([0.5] * 384, top_k=3)
