"""Tests unitarios para SQLiteHistoryStore."""

from datetime import datetime, timezone

import pytest

from adapters.outbound.history.sqlite_history_store import SQLiteHistoryStore
from core.domain.entities.message import Message, Role
from core.domain.errors import HistoryError
from infrastructure.config import HistoryConfig


@pytest.fixture
def history_store(tmp_path):
    cfg = HistoryConfig(db_path=str(tmp_path / "test_history.db"))
    return SQLiteHistoryStore(cfg)


@pytest.fixture
def history_store_limited(tmp_path):
    cfg = HistoryConfig(
        db_path=str(tmp_path / "test_history_limited.db"),
        max_messages_in_prompt=3,
    )
    return SQLiteHistoryStore(cfg)


# SC-01
async def test_append_user_with_timestamp(history_store):
    ts = datetime(2026, 4, 9, 15, 30, 0, tzinfo=timezone.utc)
    msg = Message(role=Role.USER, content="Hola", timestamp=ts)
    await history_store.append("agent1", msg)

    messages = await history_store.load("agent1")
    assert len(messages) == 1
    assert messages[0].role == Role.USER
    assert messages[0].content == "Hola"
    assert messages[0].timestamp is not None
    assert messages[0].timestamp.year == 2026
    assert messages[0].timestamp.month == 4


# SC-02
async def test_append_without_timestamp_assigns_utc_now(history_store):
    msg = Message(role=Role.ASSISTANT, content="Buenos días", timestamp=None)
    before = datetime.now(timezone.utc)
    await history_store.append("agent1", msg)
    after = datetime.now(timezone.utc)

    assert msg.timestamp is not None
    assert before <= msg.timestamp <= after

    messages = await history_store.load("agent1")
    assert len(messages) == 1
    assert messages[0].timestamp is not None


# SC-03
async def test_append_ignores_non_user_assistant_roles(history_store):
    await history_store.append("agent1", Message(role=Role.SYSTEM, content="system msg"))
    await history_store.append("agent1", Message(role=Role.TOOL, content="tool output"))
    await history_store.append("agent1", Message(role=Role.TOOL_RESULT, content="result"))

    messages = await history_store.load("agent1")
    assert messages == []


# SC-04
async def test_load_windowed_returns_last_n_asc(history_store_limited):
    for i in range(1, 6):
        await history_store_limited.append("agent1", Message(role=Role.USER, content=f"m{i}"))

    messages = await history_store_limited.load("agent1")

    assert len(messages) == 3
    assert messages[0].content == "m3"
    assert messages[1].content == "m4"
    assert messages[2].content == "m5"


# SC-05
async def test_load_no_limit_returns_all(history_store):
    for i in range(1, 6):
        await history_store.append("agent1", Message(role=Role.USER, content=f"m{i}"))

    messages = await history_store.load("agent1")

    assert len(messages) == 5
    assert [m.content for m in messages] == ["m1", "m2", "m3", "m4", "m5"]


# SC-06
async def test_load_unknown_agent_returns_empty(history_store):
    messages = await history_store.load("unknown_agent")
    assert messages == []


# SC-07
async def test_load_excludes_archived_rows(history_store):
    for i in range(3):
        await history_store.append("agent1", Message(role=Role.USER, content=f"old {i}"))

    await history_store.archive("agent1")

    await history_store.append("agent1", Message(role=Role.USER, content="new 1"))
    await history_store.append("agent1", Message(role=Role.ASSISTANT, content="new 2"))

    messages = await history_store.load("agent1")
    assert len(messages) == 2
    assert messages[0].content == "new 1"
    assert messages[1].content == "new 2"


# SC-08
async def test_load_full_ignores_max_n(history_store_limited):
    for i in range(1, 11):
        await history_store_limited.append("agent1", Message(role=Role.USER, content=f"m{i}"))

    messages = await history_store_limited.load_full("agent1")

    assert len(messages) == 10
    assert messages[0].content == "m1"
    assert messages[-1].content == "m10"


# SC-09
async def test_archive_soft_deletes_and_returns_confirmation(history_store):
    for i in range(3):
        await history_store.append("agent1", Message(role=Role.USER, content=f"msg {i}"))

    result = await history_store.archive("agent1")

    assert isinstance(result, str)
    assert len(result) > 0
    assert "agent1" in result

    messages = await history_store.load("agent1")
    assert messages == []


# SC-10
async def test_archive_raises_when_no_active_history(history_store):
    with pytest.raises(HistoryError):
        await history_store.archive("agent1")


# SC-11
async def test_archive_raises_when_already_archived(history_store):
    await history_store.append("agent1", Message(role=Role.USER, content="msg"))
    await history_store.archive("agent1")

    with pytest.raises(HistoryError):
        await history_store.archive("agent1")


# SC-12
async def test_clear_removes_all_rows(history_store):
    for i in range(3):
        await history_store.append("agent1", Message(role=Role.USER, content=f"msg {i}"))
    await history_store.archive("agent1")
    await history_store.append("agent1", Message(role=Role.USER, content="new 1"))
    await history_store.append("agent1", Message(role=Role.USER, content="new 2"))

    await history_store.clear("agent1")

    assert await history_store.load("agent1") == []
    assert await history_store.load_full("agent1") == []


# SC-13
async def test_clear_unknown_agent_no_raise(history_store):
    await history_store.clear("unknown_agent")  # should not raise


# SC-14
async def test_multi_agent_isolation(history_store):
    await history_store.append("agent_a", Message(role=Role.USER, content="soy A"))
    await history_store.append("agent_b", Message(role=Role.USER, content="soy B"))

    msgs_a = await history_store.load("agent_a")
    msgs_b = await history_store.load("agent_b")

    assert len(msgs_a) == 1
    assert msgs_a[0].content == "soy A"
    assert len(msgs_b) == 1
    assert msgs_b[0].content == "soy B"

    await history_store.archive("agent_b")

    msgs_a_after = await history_store.load("agent_a")
    assert len(msgs_a_after) == 1  # agent_a unaffected
