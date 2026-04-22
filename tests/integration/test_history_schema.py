"""
Test de integración: esquema SQLite de SQLiteHistoryStore con columnas channel + chat_id.

Cubre:
- Creación de tabla y verificación de índices en sqlite_master.
- INSERT + load con filtro por channel y chat_id.
- load sin filtros retorna todos los mensajes del agent.
- load con solo channel retorna todos los mensajes de ese canal (distintos chat_id).
- Separación entre agents distintos.
"""

from __future__ import annotations

import pytest

from adapters.outbound.history.sqlite_history_store import SQLiteHistoryStore
from core.domain.entities.message import Message, Role
from infrastructure.config import ChatHistoryConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path):
    """SQLiteHistoryStore en disco temporal (no :memory: para poder inspeccionar sqlite_master)."""
    cfg = ChatHistoryConfig(db_filename=str(tmp_path / "history_schema_test.db"))
    s = SQLiteHistoryStore(cfg)
    # Disparar creación de esquema
    await s.load("dummy_agent")
    return s


def _user_msg(content: str) -> Message:
    return Message(role=Role.USER, content=content)


def _asst_msg(content: str) -> Message:
    return Message(role=Role.ASSISTANT, content=content)


# ---------------------------------------------------------------------------
# Verificación de esquema (índices)
# ---------------------------------------------------------------------------


async def test_indices_existen(tmp_path):
    """Los tres índices requeridos deben aparecer en sqlite_master."""
    import aiosqlite

    cfg = ChatHistoryConfig(db_filename=str(tmp_path / "idx_check.db"))
    s = SQLiteHistoryStore(cfg)
    await s.load("dummy")  # crea el esquema

    async with aiosqlite.connect(str(tmp_path / "idx_check.db")) as conn:
        rows = await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'history'"
        )

    nombres_indices = {row[0] for row in rows}
    assert "idx_history_agent_channel" in nombres_indices
    assert "idx_history_uninfused" in nombres_indices
    assert "idx_history_channel_chat" in nombres_indices


async def test_columnas_channel_y_chat_id_existen(tmp_path):
    """Las columnas channel y chat_id deben estar presentes en PRAGMA table_info."""
    import aiosqlite

    cfg = ChatHistoryConfig(db_filename=str(tmp_path / "cols_check.db"))
    s = SQLiteHistoryStore(cfg)
    await s.load("dummy")

    async with aiosqlite.connect(str(tmp_path / "cols_check.db")) as conn:
        rows = await conn.execute_fetchall("PRAGMA table_info(history)")

    col_names = {row[1] for row in rows}  # columna 1 = name
    assert "channel" in col_names
    assert "chat_id" in col_names


# ---------------------------------------------------------------------------
# Insert + load filtrado por channel + chat_id
# ---------------------------------------------------------------------------


async def test_load_filtrado_channel_y_chat_id(store):
    """load(channel=X, chat_id=Y) retorna solo mensajes con esos valores exactos."""
    await store.append("agente", _user_msg("msg telegram 123"), channel="telegram", chat_id="123")
    await store.append("agente", _user_msg("msg telegram 456"), channel="telegram", chat_id="456")
    await store.append("agente", _user_msg("msg cli"), channel="cli", chat_id="")

    msgs = await store.load("agente", channel="telegram", chat_id="123")
    assert len(msgs) == 1
    assert msgs[0].content == "msg telegram 123"


async def test_load_filtrado_solo_channel(store):
    """load(channel=X) sin chat_id retorna todos los mensajes de ese canal."""
    await store.append("agente", _user_msg("tg chat A"), channel="telegram", chat_id="A")
    await store.append("agente", _user_msg("tg chat B"), channel="telegram", chat_id="B")
    await store.append("agente", _user_msg("cli msg"), channel="cli", chat_id="")

    msgs = await store.load("agente", channel="telegram")
    assert len(msgs) == 2
    contenidos = {m.content for m in msgs}
    assert "tg chat A" in contenidos
    assert "tg chat B" in contenidos


async def test_load_sin_filtros_retorna_todos(store):
    """load() sin filtros retorna todos los mensajes del agente."""
    await store.append("agente", _user_msg("msg 1"), channel="telegram", chat_id="123")
    await store.append("agente", _user_msg("msg 2"), channel="cli", chat_id="")
    await store.append("agente", _asst_msg("resp 3"), channel="telegram", chat_id="456")

    msgs = await store.load("agente")
    assert len(msgs) == 3


async def test_load_channel_chat_id_no_hay_mensajes_retorna_lista_vacia(store):
    """load() con filtros que no coinciden con ningún mensaje retorna []."""
    await store.append("agente", _user_msg("en otro canal"), channel="telegram", chat_id="111")

    msgs = await store.load("agente", channel="telegram", chat_id="999")
    assert msgs == []


# ---------------------------------------------------------------------------
# Separación entre agents
# ---------------------------------------------------------------------------


async def test_separacion_entre_agents(store):
    """Mensajes de distintos agents no se mezclan."""
    await store.append("agente_1", _user_msg("de agente 1"), channel="telegram", chat_id="c")
    await store.append("agente_2", _user_msg("de agente 2"), channel="telegram", chat_id="c")

    msgs_1 = await store.load("agente_1")
    msgs_2 = await store.load("agente_2")

    assert len(msgs_1) == 1
    assert msgs_1[0].content == "de agente 1"
    assert len(msgs_2) == 1
    assert msgs_2[0].content == "de agente 2"


# ---------------------------------------------------------------------------
# load_uninfused con filtro de channels
# ---------------------------------------------------------------------------


async def test_load_uninfused_con_channels(store):
    """load_uninfused con channels=['telegram'] retorna solo mensajes de ese canal."""
    await store.append("agente", _user_msg("tg msg"), channel="telegram", chat_id="1")
    await store.append("agente", _user_msg("cli msg"), channel="cli", chat_id="")

    msgs = await store.load_uninfused("agente", channels=["telegram"])
    assert len(msgs) == 1
    assert msgs[0].content == "tg msg"


async def test_load_uninfused_sin_channels_retorna_todos(store):
    """load_uninfused sin channels retorna todos los mensajes no infused del agente."""
    await store.append("agente", _user_msg("tg msg"), channel="telegram", chat_id="1")
    await store.append("agente", _user_msg("cli msg"), channel="cli", chat_id="")

    msgs = await store.load_uninfused("agente")
    assert len(msgs) == 2


async def test_load_uninfused_excluye_infused(store):
    """load_uninfused no retorna mensajes ya marcados como infused."""
    await store.append("agente", _user_msg("para infusar"), channel="telegram", chat_id="1")
    await store.mark_infused("agente")
    await store.append("agente", _user_msg("nuevo"), channel="telegram", chat_id="1")

    msgs = await store.load_uninfused("agente")
    assert len(msgs) == 1
    assert msgs[0].content == "nuevo"
