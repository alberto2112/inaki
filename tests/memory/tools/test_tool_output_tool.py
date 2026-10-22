"""Tests para ToolOutputTool y el archivo del output completo en SQLiteHistoryStore."""

from __future__ import annotations

import sqlite3

import pytest

from inaki.kernel.ports.history_port import ToolOutputArchive
from inaki.memory.adapters.sqlite_history_store import (
    HistoryStoreSettings,
    SQLiteHistoryStore,
)
from inaki.memory.tools.tool_output_tool import _PAGE_CHARS, ToolOutputTool
from inaki.shared.message import Message, Role


@pytest.fixture
def store(tmp_path):
    return SQLiteHistoryStore(HistoryStoreSettings(db_filename=str(tmp_path / "h.db")))


async def _archivar(store: SQLiteHistoryStore, agent_id: str, ref: str, completo: str) -> None:
    await store.append(
        agent_id,
        Message(role=Role.TOOL, content="cabeza…cola", tool_call_id="call_1"),
        channel="telegram",
        chat_id="100",
        full_output=ToolOutputArchive(ref=ref, content=completo),
    )


async def test_devuelve_el_output_completo_por_ref(store):
    completo = 'HTML inútil\n{"exit": 0}'
    await _archivar(store, "agent1", "abc123", completo)
    tool = ToolOutputTool(history=store, agent_id="agent1")

    res = await tool.execute(ref="abc123")

    assert res.success is True
    assert '{"exit": 0}' in res.output
    assert res.output.endswith(completo)
    assert f"of {len(completo)}." in res.output


async def test_el_load_solo_ve_el_content_truncado(store):
    """El completo NUNCA entra al contexto por la ventana: solo por la tool."""
    await store.append(
        "agent1",
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[{"id": "call_1", "function": {"name": "x", "arguments": "{}"}}],
        ),
        channel="telegram",
        chat_id="100",
    )
    await _archivar(store, "agent1", "abc123", "X" * 5000)

    mensajes = await store.load("agent1")

    assert [m.content for m in mensajes] == ["", "cabeza…cola"]


async def test_scopea_por_agent_id(store):
    await _archivar(store, "agent2", "abc123", "secreto de otro agente")
    tool = ToolOutputTool(history=store, agent_id="agent1")

    res = await tool.execute(ref="abc123")

    assert "secreto" not in res.output
    assert "No stored output" in res.output


async def test_ref_ausente_dice_no_lo_tengo_no_que_no_existio(store):
    tool = ToolOutputTool(history=store, agent_id="agent1")

    res = await tool.execute(ref="inexistente")

    assert res.success is True
    assert "proves NOTHING" in res.output


async def test_sin_ref_falla_sin_reintento(store):
    tool = ToolOutputTool(history=store, agent_id="agent1")

    res = await tool.execute()

    assert res.success is False
    assert res.retryable is False


async def test_pagina_los_outputs_largos(store):
    completo = "A" * _PAGE_CHARS + "B" * 10
    await _archivar(store, "agent1", "abc123", completo)
    tool = ToolOutputTool(history=store, agent_id="agent1")

    primera = await tool.execute(ref="abc123")
    segunda = await tool.execute(ref="abc123", offset=_PAGE_CHARS)

    assert "B" not in primera.output.splitlines()[1]
    assert f"offset={_PAGE_CHARS}" in primera.output
    assert segunda.output.splitlines()[1] == "B" * 10
    assert "continues" not in segunda.output


async def test_offset_fuera_de_rango(store):
    await _archivar(store, "agent1", "abc123", "corto")
    tool = ToolOutputTool(history=store, agent_id="agent1")

    res = await tool.execute(ref="abc123", offset=99)

    assert res.success is False


async def test_trim_purga_el_output_con_su_fila(store):
    """Retención acoplada: el completo vive y muere con la fila del historial."""
    await store.append("agent1", Message(role=Role.USER, content="viejo"), "telegram", "100")
    await _archivar(store, "agent1", "abc123", "completo")
    await store.append("agent1", Message(role=Role.ASSISTANT, content="ok"), "telegram", "100")
    await store.append("agent1", Message(role=Role.USER, content="nuevo"), "telegram", "100")
    await store.append("agent1", Message(role=Role.ASSISTANT, content="dale"), "telegram", "100")

    await store.trim("agent1", keep_last=2)

    assert await store.get_tool_output("agent1", "abc123") is None


async def test_migra_una_db_sin_las_columnas_nuevas(tmp_path):
    db = tmp_path / "vieja.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,"
            " role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,"
            " infused INTEGER NOT NULL DEFAULT 0, channel TEXT NOT NULL DEFAULT '',"
            " chat_id TEXT NOT NULL DEFAULT '', tool_calls TEXT, tool_call_id TEXT)"
        )
    store = SQLiteHistoryStore(HistoryStoreSettings(db_filename=str(db)))

    await _archivar(store, "agent1", "abc123", "completo")

    assert await store.get_tool_output("agent1", "abc123") == "completo"
