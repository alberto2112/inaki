"""Tests unitarios para SQLiteHistoryStore."""

from datetime import datetime, timezone

import pytest

from adapters.outbound.history.sqlite_history_store import SQLiteHistoryStore
from core.domain.entities.message import Message, Role
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


# SC-07 (ex-archived, ahora trim)
async def test_trim_keeps_last_n_messages(history_store):
    for i in range(1, 6):
        await history_store.append("agent1", Message(role=Role.USER, content=f"msg {i}"))

    await history_store.trim("agent1", keep_last=2)

    messages = await history_store.load("agent1")
    assert len(messages) == 2
    assert messages[0].content == "msg 4"
    assert messages[1].content == "msg 5"


async def test_trim_noop_when_keep_last_greater_than_count(history_store):
    for i in range(1, 4):
        await history_store.append("agent1", Message(role=Role.USER, content=f"msg {i}"))

    await history_store.trim("agent1", keep_last=10)

    messages = await history_store.load("agent1")
    assert len(messages) == 3


async def test_trim_zero_is_noop(history_store):
    """keep_last <= 0 es no-op defensivo, NUNCA borra todo."""
    for i in range(1, 4):
        await history_store.append("agent1", Message(role=Role.USER, content=f"msg {i}"))

    await history_store.trim("agent1", keep_last=0)

    messages = await history_store.load("agent1")
    assert len(messages) == 3


async def test_trim_isolated_per_agent(history_store):
    for i in range(1, 6):
        await history_store.append("agent_a", Message(role=Role.USER, content=f"a{i}"))
    for i in range(1, 4):
        await history_store.append("agent_b", Message(role=Role.USER, content=f"b{i}"))

    await history_store.trim("agent_a", keep_last=2)

    msgs_a = await history_store.load("agent_a")
    msgs_b = await history_store.load("agent_b")
    assert len(msgs_a) == 2
    assert len(msgs_b) == 3  # agent_b intacto


# ---------------------------------------------------------------------------
# infused flag — load_uninfused + mark_infused
# ---------------------------------------------------------------------------

async def test_new_messages_are_uninfused_by_default(history_store):
    for i in range(3):
        await history_store.append("agent1", Message(role=Role.USER, content=f"msg {i}"))

    uninfused = await history_store.load_uninfused("agent1")
    assert len(uninfused) == 3


async def test_mark_infused_moves_messages_out_of_uninfused(history_store):
    for i in range(3):
        await history_store.append("agent1", Message(role=Role.USER, content=f"msg {i}"))

    affected = await history_store.mark_infused("agent1")
    assert affected == 3

    uninfused = await history_store.load_uninfused("agent1")
    assert uninfused == []

    # load() sigue devolviendo todo — el prompt builder necesita el contexto
    all_msgs = await history_store.load("agent1")
    assert len(all_msgs) == 3


async def test_mark_infused_returns_zero_when_nothing_pending(history_store):
    for i in range(2):
        await history_store.append("agent1", Message(role=Role.USER, content=f"msg {i}"))
    await history_store.mark_infused("agent1")

    # Segundo mark sobre mensajes ya infused → 0 filas afectadas
    affected = await history_store.mark_infused("agent1")
    assert affected == 0


async def test_mark_infused_only_touches_current_uninfused(history_store):
    """
    Mensajes añadidos DESPUÉS de un mark_infused anterior deben seguir como
    uninfused hasta el próximo mark.
    """
    await history_store.append("agent1", Message(role=Role.USER, content="old 1"))
    await history_store.append("agent1", Message(role=Role.USER, content="old 2"))
    await history_store.mark_infused("agent1")

    await history_store.append("agent1", Message(role=Role.USER, content="new 1"))
    await history_store.append("agent1", Message(role=Role.USER, content="new 2"))

    uninfused = await history_store.load_uninfused("agent1")
    assert [m.content for m in uninfused] == ["new 1", "new 2"]


async def test_mark_infused_isolated_per_agent(history_store):
    await history_store.append("agent_a", Message(role=Role.USER, content="a1"))
    await history_store.append("agent_b", Message(role=Role.USER, content="b1"))

    await history_store.mark_infused("agent_a")

    uninfused_a = await history_store.load_uninfused("agent_a")
    uninfused_b = await history_store.load_uninfused("agent_b")
    assert uninfused_a == []
    assert len(uninfused_b) == 1  # agent_b intacto


# ---------------------------------------------------------------------------
# Migración: DB legacy sin columna `infused`
# ---------------------------------------------------------------------------

async def test_migration_adds_infused_column_and_marks_existing_rows(tmp_path):
    """
    Simula una DB preexistente sin la columna `infused`: el primer uso del
    store debe añadir la columna y marcar todo lo existente como infused=1
    (asumimos estado estable previo al cambio).
    """
    import aiosqlite

    db_path = tmp_path / "legacy_history.db"

    # 1. Creamos una DB con el schema viejo (sin columna `infused`)
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            """
            CREATE TABLE history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id   TEXT    NOT NULL,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                created_at TEXT    NOT NULL,
                archived   INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Insertamos mensajes "legacy" — el cliente viejo no conocía `infused`
        for i in range(3):
            await conn.execute(
                "INSERT INTO history (agent_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                ("agent1", "user", f"legacy {i}", "2026-04-01T00:00:00+00:00"),
            )
        await conn.commit()

    # 2. Abrimos con el store nuevo — debe migrar al primer _ensure_schema
    cfg = HistoryConfig(db_path=str(db_path))
    store = SQLiteHistoryStore(cfg)

    # 3. load_uninfused debe devolver 0 (migración marcó todo como infused=1)
    uninfused = await store.load_uninfused("agent1")
    assert uninfused == []

    # 4. load() sigue viendo los 3 mensajes legacy
    all_msgs = await store.load("agent1")
    assert len(all_msgs) == 3

    # 5. Nuevos mensajes tras la migración arrancan como uninfused
    await store.append("agent1", Message(role=Role.USER, content="post migration"))
    new_uninfused = await store.load_uninfused("agent1")
    assert len(new_uninfused) == 1
    assert new_uninfused[0].content == "post migration"


async def test_migration_idempotent(history_store):
    """Re-aplicar la migración sobre una DB ya migrada no debe romper ni duplicar."""
    await history_store.append("agent1", Message(role=Role.USER, content="hello"))

    # Forzar una segunda pasada por _ensure_schema
    async with history_store._conn() as conn:
        await history_store._ensure_schema(conn)

    # Estado coherente
    msgs = await history_store.load("agent1")
    assert len(msgs) == 1
    uninfused = await history_store.load_uninfused("agent1")
    assert len(uninfused) == 1  # Fresh row, sigue uninfused


# SC-08
async def test_load_full_ignores_max_n(history_store_limited):
    for i in range(1, 11):
        await history_store_limited.append("agent1", Message(role=Role.USER, content=f"m{i}"))

    messages = await history_store_limited.load_full("agent1")

    assert len(messages) == 10
    assert messages[0].content == "m1"
    assert messages[-1].content == "m10"


# SC-12
async def test_clear_removes_all_rows(history_store):
    for i in range(5):
        await history_store.append("agent1", Message(role=Role.USER, content=f"msg {i}"))

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

    await history_store.clear("agent_b")

    msgs_a_after = await history_store.load("agent_a")
    assert len(msgs_a_after) == 1  # agent_a unaffected
