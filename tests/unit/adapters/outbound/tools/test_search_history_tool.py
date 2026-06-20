"""Tests para SearchHistoryTool (builtin sobre IHistoryStore)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from adapters.outbound.history.sqlite_history_store import (
    HistoryStoreSettings,
    SQLiteHistoryStore,
)
from adapters.outbound.tools.search_history_tool import SearchHistoryTool
from core.domain.entities.message import Message, Role


@pytest.fixture
def store(tmp_path):
    return SQLiteHistoryStore(HistoryStoreSettings(db_filename=str(tmp_path / "h.db")))


async def _seed(store: SQLiteHistoryStore) -> None:
    await store.append(
        "agent1", Message(role=Role.USER, content="hola por telegram"),
        channel="telegram", chat_id="100",
    )
    await store.append(
        "agent1", Message(role=Role.ASSISTANT, content="respuesta del bot"),
        channel="telegram", chat_id="100",
    )
    # Otro agente — la tool de agent1 NO debe verlo.
    await store.append(
        "agent2", Message(role=Role.USER, content="secreto de otro agente"),
        channel="telegram", chat_id="100",
    )


async def test_tool_devuelve_mensajes_formateados(store):
    await _seed(store)
    tool = SearchHistoryTool(history=store, agent_id="agent1")
    res = await tool.execute(query="telegram")

    assert res.success is True
    assert "hola por telegram" in res.output
    # El scope de origen aparece en el output.
    assert "telegram" in res.output and "100" in res.output


async def test_tool_scopea_por_agent_id(store):
    """Defensa clave: la tool de agent1 nunca filtra historial de agent2."""
    await _seed(store)
    tool = SearchHistoryTool(history=store, agent_id="agent1")
    res = await tool.execute()

    assert res.success is True
    assert "secreto de otro agente" not in res.output


async def test_tool_sin_resultados_es_success(store):
    await _seed(store)
    tool = SearchHistoryTool(history=store, agent_id="agent1")
    res = await tool.execute(query="no_existe_este_texto")

    assert res.success is True
    assert "No messages found" in res.output


async def test_tool_rol_invalido_falla(store):
    tool = SearchHistoryTool(history=store, agent_id="agent1")
    res = await tool.execute(role="system")

    assert res.success is False
    assert "role" in res.output.lower()


async def test_tool_clampa_limit_al_maximo():
    """limit > 100 se acota a 100 antes de tocar el store."""
    mock_store = AsyncMock()
    mock_store.search.return_value = []
    tool = SearchHistoryTool(history=mock_store, agent_id="agent1")

    await tool.execute(limit=9999)

    _, kwargs = mock_store.search.call_args
    assert kwargs["limit"] == 100


async def test_tool_propaga_filtros_al_store():
    mock_store = AsyncMock()
    mock_store.search.return_value = []
    tool = SearchHistoryTool(history=mock_store, agent_id="agent1")

    await tool.execute(query="x", role="user", channel="telegram", chat_id="100")

    args, kwargs = mock_store.search.call_args
    assert args[0] == "agent1"  # agent_id siempre primero, hardcodeado por el container
    assert kwargs == {
        "query": "x",
        "role": "user",
        "channel": "telegram",
        "chat_id": "100",
        "limit": 20,
    }
