"""Tests unitarios para ConsolidateMemoryUseCase — transaccionalidad crítica."""

from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, call

from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from core.domain.entities.message import Message, Role
from core.domain.errors import ConsolidationError


@pytest.fixture
def use_case(mock_llm, mock_memory, mock_embedder, mock_history):
    return ConsolidateMemoryUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        history=mock_history,
        agent_id="test",
    )


@pytest.fixture
def messages_in_history(mock_history):
    mock_history.load_full.return_value = [
        Message(role=Role.USER, content="me gusta Python"),
        Message(role=Role.ASSISTANT, content="Anotado."),
    ]


async def test_consolidation_archives_on_success(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    mock_llm.complete.return_value = '[{"content": "Le gusta Python", "relevance": 0.9, "tags": ["tech"]}]'

    result = await use_case.execute()

    mock_memory.store.assert_called_once()
    mock_history.archive.assert_called_once_with("test")
    mock_history.clear.assert_called_once_with("test")
    assert "1 recuerdo" in result


async def test_consolidation_does_not_archive_on_llm_failure(use_case, mock_llm, mock_history, messages_in_history):
    mock_llm.complete.side_effect = Exception("LLM timeout")

    with pytest.raises(ConsolidationError):
        await use_case.execute()

    mock_history.archive.assert_not_called()
    mock_history.clear.assert_not_called()


async def test_consolidation_does_not_archive_on_store_failure(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    mock_llm.complete.return_value = '[{"content": "Le gusta Python", "relevance": 0.9, "tags": []}]'
    mock_memory.store.side_effect = Exception("DB error")

    with pytest.raises(ConsolidationError):
        await use_case.execute()

    mock_history.archive.assert_not_called()


async def test_consolidation_returns_message_when_history_empty(use_case, mock_history):
    mock_history.load_full.return_value = []
    result = await use_case.execute()
    assert "vacío" in result


async def test_consolidation_handles_empty_facts_list(use_case, mock_llm, mock_history, messages_in_history):
    """LLM dice no hay recuerdos relevantes → archivamos igual."""
    mock_llm.complete.return_value = "[]"
    result = await use_case.execute()
    mock_history.archive.assert_called_once()


async def test_consolidation_strips_markdown_json(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    """El LLM a veces envuelve el JSON en ```json ... ```"""
    mock_llm.complete.return_value = '```json\n[{"content": "test", "relevance": 0.8, "tags": []}]\n```'
    await use_case.execute()
    mock_memory.store.assert_called_once()


async def test_consolidation_raises_on_invalid_json(use_case, mock_llm, mock_history, messages_in_history):
    mock_llm.complete.return_value = "esto no es json"
    with pytest.raises(ConsolidationError):
        await use_case.execute()
    mock_history.archive.assert_not_called()


# SC-15
async def test_consolidation_formats_message_with_timestamp(use_case, mock_llm, mock_memory, mock_history):
    ts = datetime(2026, 4, 9, 15, 30, 0, tzinfo=timezone.utc)
    mock_history.load_full.return_value = [
        Message(role=Role.USER, content="prefiero café sin azúcar", timestamp=ts),
    ]
    mock_llm.complete.return_value = "[]"

    await use_case.execute()

    call_args = mock_llm.complete.call_args
    system_prompt = call_args.kwargs.get("system_prompt") or call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["system_prompt"]
    assert "user [2026-04-09T15:30:00Z]: prefiero café sin azúcar" in system_prompt


# SC-16
async def test_consolidation_formats_message_without_timestamp(use_case, mock_llm, mock_memory, mock_history):
    mock_history.load_full.return_value = [
        Message(role=Role.USER, content="prefiero café sin azúcar", timestamp=None),
    ]
    mock_llm.complete.return_value = "[]"

    await use_case.execute()

    call_args = mock_llm.complete.call_args
    system_prompt = call_args.kwargs.get("system_prompt") or call_args.kwargs["system_prompt"]
    assert "user: prefiero café sin azúcar" in system_prompt
    assert "[" not in system_prompt.split("user:")[1].split("\n")[0]


# SC-17
async def test_consolidation_sets_created_at_from_llm_timestamp(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    mock_llm.complete.return_value = '[{"content": "test", "relevance": 0.9, "tags": [], "timestamp": "2026-04-09T15:30:00Z"}]'

    await use_case.execute()

    entry = mock_memory.store.call_args.args[0]
    assert entry.created_at == datetime(2026, 4, 9, 15, 30, 0, tzinfo=timezone.utc)


# SC-18
async def test_consolidation_falls_back_to_now_when_no_timestamp(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    mock_llm.complete.return_value = '[{"content": "test", "relevance": 0.9, "tags": []}]'
    before = datetime.now(timezone.utc)

    await use_case.execute()

    after = datetime.now(timezone.utc)
    entry = mock_memory.store.call_args.args[0]
    assert before <= entry.created_at <= after
