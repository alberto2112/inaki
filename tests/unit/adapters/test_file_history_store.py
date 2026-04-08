"""Tests unitarios para FileHistoryStore."""

import pytest
import tempfile
from pathlib import Path

from adapters.outbound.history.file_history_store import FileHistoryStore
from core.domain.entities.message import Message, Role
from infrastructure.config import HistoryConfig


@pytest.fixture
def history_store(tmp_path):
    cfg = HistoryConfig(
        active_dir=str(tmp_path / "active"),
        archive_dir=str(tmp_path / "archive"),
    )
    return FileHistoryStore(cfg)


async def test_load_empty_history(history_store):
    messages = await history_store.load("test_agent")
    assert messages == []


async def test_append_and_load(history_store):
    await history_store.append("test_agent", Message(role=Role.USER, content="Hola"))
    await history_store.append("test_agent", Message(role=Role.ASSISTANT, content="Hola también"))

    messages = await history_store.load("test_agent")
    assert len(messages) == 2
    assert messages[0].role == Role.USER
    assert messages[0].content == "Hola"
    assert messages[1].role == Role.ASSISTANT
    assert messages[1].content == "Hola también"


async def test_tool_messages_are_ignored(history_store):
    """Los mensajes de tipo TOOL no deben persistirse."""
    await history_store.append("test_agent", Message(role=Role.USER, content="user msg"))
    await history_store.append("test_agent", Message(role=Role.TOOL, content="tool output"))
    await history_store.append("test_agent", Message(role=Role.ASSISTANT, content="assistant msg"))

    messages = await history_store.load("test_agent")
    assert len(messages) == 2  # Solo user y assistant
    assert all(m.role in (Role.USER, Role.ASSISTANT) for m in messages)


async def test_archive_moves_file(history_store, tmp_path):
    await history_store.append("test_agent", Message(role=Role.USER, content="test"))

    archive_path = await history_store.archive("test_agent")

    assert Path(archive_path).exists()
    assert not (tmp_path / "active" / "test_agent.txt").exists()


async def test_clear_deletes_active_file(history_store, tmp_path):
    await history_store.append("test_agent", Message(role=Role.USER, content="test"))
    await history_store.clear("test_agent")

    messages = await history_store.load("test_agent")
    assert messages == []


async def test_archive_raises_when_no_history(history_store):
    from core.domain.errors import HistoryError
    with pytest.raises(HistoryError):
        await history_store.archive("nonexistent_agent")


async def test_multiple_agents_independent(history_store):
    """Dos agentes tienen historiales independientes."""
    await history_store.append("agent_a", Message(role=Role.USER, content="soy A"))
    await history_store.append("agent_b", Message(role=Role.USER, content="soy B"))

    msgs_a = await history_store.load("agent_a")
    msgs_b = await history_store.load("agent_b")

    assert msgs_a[0].content == "soy A"
    assert msgs_b[0].content == "soy B"
