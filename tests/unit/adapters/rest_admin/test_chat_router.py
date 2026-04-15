"""Tests para el router de chat admin — endpoints POST /turn, GET /history, DELETE /history.

Cubre tareas 5.1, 5.2, 5.3, 5.4 (TEST).

Escenarios cubiertos (spec admin-chat/spec.md):
  POST /admin/chat/turn:
    - Happy path → 200 con reply
    - Sin X-Admin-Key → 401
    - agent_id inválido → 404 con error_code agent_not_found
    - session_id ausente → 422
    - message vacío → 422
    - run_agent.execute() lanza excepción → 500

  Diseño §A3 — ChannelContext:
    - set_channel_context("cli", session_id) se llama y se resetea a None (try/finally)
    - set_channel_context se llama incluso si execute() falla (finally garantiza reset)

  GET /admin/chat/history:
    - Happy path → 200 con lista de mensajes
    - Historia vacía → 200 con lista vacía
    - agent_id inválido → 404
    - Sin auth → 401

  DELETE /admin/chat/history:
    - Happy path → 204
    - agent_id inválido → 404
    - Sin auth → 401
    - GET posterior a DELETE → lista vacía (fresh state)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from httpx import ASGITransport, AsyncClient

from adapters.inbound.rest.admin.app import create_admin_app
from core.domain.entities.message import Message, Role
from core.domain.errors import UnknownAgentError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_run_agent() -> MagicMock:
    """Mock de RunAgentUseCase con execute/get_history/clear_history."""
    agent = MagicMock()
    agent.execute = AsyncMock(return_value="Hola, ¿en qué te ayudo?")
    agent.get_history = AsyncMock(return_value=[])
    agent.clear_history = AsyncMock(return_value=None)
    return agent


@pytest.fixture
def mock_agent_container(mock_run_agent: MagicMock) -> MagicMock:
    """Mock de AgentContainer con run_agent y set_channel_context."""
    container = MagicMock()
    container.run_agent = mock_run_agent
    container.set_channel_context = MagicMock(return_value=None)
    return container


@pytest.fixture
def mock_app_container(mock_agent_container: MagicMock) -> MagicMock:
    """Mock de AppContainer con agents dict."""
    app_container = MagicMock()
    app_container.agents = {"dev": mock_agent_container}
    return app_container


@pytest.fixture
def chat_app(mock_app_container: MagicMock):
    """FastAPI app del admin server con agent 'dev' registrado."""
    return create_admin_app(mock_app_container, admin_auth_key="clave-test")


VALID_KEY = {"X-Admin-Key": "clave-test"}
TURN_BODY = {"agent_id": "dev", "session_id": "550e8400-e29b-41d4-a716-446655440000", "message": "hola"}


# ---------------------------------------------------------------------------
# POST /admin/chat/turn — happy path (5.1)
# ---------------------------------------------------------------------------


async def test_post_turn_happy_path(chat_app, mock_run_agent) -> None:
    """POST /turn con datos válidos → 200 con reply."""
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.post("/admin/chat/turn", json=TURN_BODY, headers=VALID_KEY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "Hola, ¿en qué te ayudo?"
    assert data["agent_id"] == "dev"
    assert data["session_id"] == TURN_BODY["session_id"]
    mock_run_agent.execute.assert_awaited_once_with("hola")


# ---------------------------------------------------------------------------
# POST /admin/chat/turn — sin auth (5.1)
# ---------------------------------------------------------------------------


async def test_post_turn_sin_auth_401(chat_app) -> None:
    """POST /turn sin X-Admin-Key → 401."""
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.post("/admin/chat/turn", json=TURN_BODY)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /admin/chat/turn — agente inválido (5.1)
# ---------------------------------------------------------------------------


async def test_post_turn_agente_invalido_404(chat_app) -> None:
    """POST /turn con agent_id inexistente → 404 con error_code agent_not_found."""
    body = {**TURN_BODY, "agent_id": "ghost"}
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.post("/admin/chat/turn", json=body, headers=VALID_KEY)
    assert resp.status_code == 404
    data = resp.json()
    assert data["detail"]["error_code"] == "agent_not_found"


# ---------------------------------------------------------------------------
# POST /admin/chat/turn — payload inválido (5.1)
# ---------------------------------------------------------------------------


async def test_post_turn_sin_session_id_422(chat_app) -> None:
    """POST /turn sin session_id → 422 (validación Pydantic)."""
    body = {"agent_id": "dev", "message": "hola"}
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.post("/admin/chat/turn", json=body, headers=VALID_KEY)
    assert resp.status_code == 422


async def test_post_turn_message_vacio_422(chat_app) -> None:
    """POST /turn con message vacío → 422."""
    body = {**TURN_BODY, "message": ""}
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.post("/admin/chat/turn", json=body, headers=VALID_KEY)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /admin/chat/turn — error interno (5.1)
# ---------------------------------------------------------------------------


async def test_post_turn_error_interno_500(chat_app, mock_run_agent) -> None:
    """POST /turn cuando execute() lanza excepción → 500."""
    mock_run_agent.execute.side_effect = RuntimeError("LLM falló")
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.post("/admin/chat/turn", json=TURN_BODY, headers=VALID_KEY)
    assert resp.status_code == 500
    data = resp.json()
    assert data["detail"]["error_code"] == "internal_error"


# ---------------------------------------------------------------------------
# POST /admin/chat/turn — ChannelContext (5.2)
# ---------------------------------------------------------------------------


async def test_post_turn_set_channel_context_llamado(
    chat_app, mock_agent_container, mock_run_agent
) -> None:
    """POST /turn llama set_channel_context("cli", session_id) y lo resetea a None (try/finally)."""
    from core.domain.value_objects.channel_context import ChannelContext

    session_id = TURN_BODY["session_id"]
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        await ac.post("/admin/chat/turn", json=TURN_BODY, headers=VALID_KEY)

    # Verificar set_channel_context fue llamado con el ChannelContext correcto y luego con None
    calls = mock_agent_container.set_channel_context.call_args_list
    assert len(calls) == 2
    ctx_pasado = calls[0].args[0]
    assert ctx_pasado.channel_type == "cli"
    assert ctx_pasado.user_id == session_id
    assert calls[1].args[0] is None


async def test_post_turn_channel_context_reset_en_error(
    chat_app, mock_agent_container, mock_run_agent
) -> None:
    """set_channel_context(None) se llama incluso si execute() falla (garantía try/finally)."""
    mock_run_agent.execute.side_effect = RuntimeError("crash")
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        await ac.post("/admin/chat/turn", json=TURN_BODY, headers=VALID_KEY)

    calls = mock_agent_container.set_channel_context.call_args_list
    # Debe haberse llamado al menos una vez con None (el finally)
    assert any(c.args[0] is None for c in calls), "set_channel_context(None) no fue llamado en finally"


# ---------------------------------------------------------------------------
# GET /admin/chat/history — happy path (5.3)
# ---------------------------------------------------------------------------


async def test_get_history_happy(chat_app, mock_run_agent) -> None:
    """GET /history con agente válido → 200 con mensajes (incluyendo timestamp)."""
    from datetime import datetime

    msgs = [
        Message(role=Role.USER, content="hola", timestamp=datetime(2026, 1, 1, 12, 0)),
        Message(role=Role.ASSISTANT, content="¡hola!", timestamp=datetime(2026, 1, 1, 12, 1)),
    ]
    mock_run_agent.get_history.return_value = msgs
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.get("/admin/chat/history", params={"agent_id": "dev"}, headers=VALID_KEY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "dev"
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "hola"
    assert data["messages"][1]["role"] == "assistant"
    # Correction 1: timestamp debe estar presente en la respuesta
    assert "timestamp" in data["messages"][0]
    assert data["messages"][0]["timestamp"] is not None
    assert "timestamp" in data["messages"][1]


async def test_get_history_vacia(chat_app, mock_run_agent) -> None:
    """GET /history con agente sin mensajes → 200 con lista vacía."""
    mock_run_agent.get_history.return_value = []
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.get("/admin/chat/history", params={"agent_id": "dev"}, headers=VALID_KEY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"] == []


async def test_get_history_agente_invalido_404(chat_app) -> None:
    """GET /history con agent_id desconocido → 404."""
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.get("/admin/chat/history", params={"agent_id": "ghost"}, headers=VALID_KEY)
    assert resp.status_code == 404


async def test_get_history_sin_auth_401(chat_app) -> None:
    """GET /history sin X-Admin-Key → 401."""
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.get("/admin/chat/history", params={"agent_id": "dev"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /admin/chat/history — happy path (5.4)
# ---------------------------------------------------------------------------


async def test_delete_history_happy(chat_app, mock_run_agent) -> None:
    """DELETE /history con agente válido → 204."""
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.delete("/admin/chat/history", params={"agent_id": "dev"}, headers=VALID_KEY)
    assert resp.status_code == 204
    mock_run_agent.clear_history.assert_awaited_once()


async def test_delete_history_agente_invalido_404(chat_app) -> None:
    """DELETE /history con agent_id desconocido → 404."""
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.delete(
            "/admin/chat/history", params={"agent_id": "ghost"}, headers=VALID_KEY
        )
    assert resp.status_code == 404


async def test_delete_history_sin_auth_401(chat_app) -> None:
    """DELETE /history sin X-Admin-Key → 401."""
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.delete("/admin/chat/history", params={"agent_id": "dev"})
    assert resp.status_code == 401


async def test_get_history_vacia_tras_delete(chat_app, mock_run_agent) -> None:
    """Después de DELETE, GET history devuelve lista vacía (fresh turn after DELETE)."""
    mock_run_agent.get_history.return_value = []
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        del_resp = await ac.delete(
            "/admin/chat/history", params={"agent_id": "dev"}, headers=VALID_KEY
        )
        get_resp = await ac.get(
            "/admin/chat/history", params={"agent_id": "dev"}, headers=VALID_KEY
        )
    assert del_resp.status_code == 204
    assert get_resp.status_code == 200
    assert get_resp.json()["messages"] == []


# ---------------------------------------------------------------------------
# Correction 2 — GET /admin/agents
# ---------------------------------------------------------------------------


async def test_list_agents_happy(chat_app) -> None:
    """GET /admin/agents → 200 con lista de agentes registrados."""
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.get("/admin/agents", headers=VALID_KEY)
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data
    assert "dev" in data["agents"]


async def test_list_agents_sin_auth_401(chat_app) -> None:
    """GET /admin/agents sin X-Admin-Key → 401."""
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as ac:
        resp = await ac.get("/admin/agents")
    assert resp.status_code == 401
