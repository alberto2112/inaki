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


# ---------------------------------------------------------------------------
# Horizonte de retención — la tool no puede afirmar inexistencia
# ---------------------------------------------------------------------------


async def test_resultado_vacio_declara_el_horizonte_y_no_afirma_inexistencia(store):
    """Regresión de `search-history-retention-horizon`: la consolidación BORRA
    filas, así que un resultado vacío significa "ya no lo tengo", no "no pasó".

    Sin esta frase el agente convertía la falsa ausencia en una confesión que no
    podía sostener ("te dije que lo hice y no lo hice").
    """
    await _seed(store)
    tool = SearchHistoryTool(history=store, agent_id="agent1")
    res = await tool.execute(query="no_existe_este_texto")

    assert res.success is True
    assert "No messages found" in res.output
    assert "SCOPE OF THIS RECORD" in res.output
    assert "proves NOTHING" in res.output
    assert "COMPLETE coverage" in res.output
    # El horizonte real del scope viaja en el output, no una frase genérica.
    horizonte = await store.retention_horizon("agent1")
    assert horizonte is not None
    assert horizonte.isoformat() in res.output


async def test_resultado_con_hallazgos_tambien_declara_el_horizonte(store):
    """Un resultado no vacío tampoco es el pasado completo: puede haber
    coincidencias más viejas ya borradas."""
    await _seed(store)
    tool = SearchHistoryTool(history=store, agent_id="agent1")
    res = await tool.execute(query="telegram")

    assert "hola por telegram" in res.output
    assert "SCOPE OF THIS RECORD" in res.output


async def test_horizonte_es_el_peor_scope_no_el_menos_podado(store):
    """Sin `chat_id` la búsqueda cubre VARIOS scopes, y `trim` borra POR scope.

    El horizonte debe ser el punto desde el que la cobertura está completa en
    todos (el MAX de los primeros mensajes), no el MIN global — que es el del
    scope menos podado y mentiría sobre los demás. Es el caso real del 2026-07-31:
    el MIN global daba mayo mientras el chat del usuario solo llegaba a esa
    mañana, y con "mayo" el agente igual habría concluido "no ocurrió".
    """
    await _seed(store)  # chat 100
    await store.append(
        "agent1", Message(role=Role.USER, content="otro chat"),
        channel="telegram", chat_id="999",
    )

    chat_100 = await store.retention_horizon("agent1", chat_id="100")
    chat_999 = await store.retention_horizon("agent1", chat_id="999")
    sin_filtro = await store.retention_horizon("agent1")
    assert chat_100 is not None and chat_999 is not None and sin_filtro is not None
    assert chat_999 > chat_100  # el chat 999 nació después
    assert sin_filtro == chat_999  # el peor caso manda, no el más antiguo

    tool = SearchHistoryTool(history=store, agent_id="agent1")
    res = await tool.execute(query="nada")
    assert chat_999.isoformat() in res.output

    # Con el filtro puesto, el horizonte es exactamente el de ese scope.
    scoped = await tool.execute(query="nada", chat_id="100")
    assert chat_100.isoformat() in scoped.output


async def test_scope_sin_filas_lo_dice_explicitamente(store):
    """Sin filas no hay horizonte: la búsqueda no puede confirmar ni negar nada."""
    tool = SearchHistoryTool(history=store, agent_id="agent1")
    res = await tool.execute(query="lo que sea")

    assert res.success is True
    assert "NO stored messages at all" in res.output


async def test_fallo_del_horizonte_no_rompe_la_busqueda():
    """Si el store no puede dar el horizonte, la búsqueda sigue devolviendo sus
    resultados — degradada, pero nunca afirmando inexistencia."""
    mock_store = AsyncMock()
    mock_store.search.return_value = []
    mock_store.retention_horizon.side_effect = RuntimeError("db caída")
    tool = SearchHistoryTool(history=mock_store, agent_id="agent1")

    res = await tool.execute(query="x")

    assert res.success is True
    assert "could not be determined" in res.output
    assert "not evidence" in res.output
