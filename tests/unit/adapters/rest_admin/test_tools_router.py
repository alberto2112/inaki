"""Tests para el router de tools admin.

Cubre los tres endpoints:
  GET  /admin/tool/list    — lista tools del agente
  POST /admin/tool/invoke  — invoca tool del agente
  POST /admin/send         — envía mensaje via ChannelOutboundRegistry

Escenarios cubiertos:
  GET /admin/tool/list:
    - Happy path — lista tools del agente
    - agent_id desconocido → 404 con error_code agent_not_found
    - Sin X-Admin-Key → 401

  POST /admin/tool/invoke:
    - Happy path — tool ejecutada con éxito
    - Tool no registrada → 200 con success=False (el registry maneja esto)
    - Agente desconocido → 404
    - Exception inesperada en execute() → 500
    - Sin auth → 401

  POST /admin/send:
    - TEXT happy path
    - PHOTO happy path
    - ALBUM happy path
    - kind no soportado por el adapter → 422 unsupported_kind
    - Canal no registrado → 404 channel_not_registered
    - FileNotFoundError en adapter.send() → 404 source_not_found
    - RuntimeError (canal no disponible) → 503 channel_unavailable
    - Validaciones pydantic: kind=text sin text → 422
    - Validaciones pydantic: kind=photo con 2 sources → 422
    - Sin auth → 401
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from adapters.inbound.rest.admin.app import create_admin_app
from core.domain.value_objects.outbound_kind import OutboundKind

VALID_KEY = {"X-Admin-Key": "clave-test"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tool_schema(name: str, description: str) -> dict:
    """Helper para construir un schema OpenAI de tool fake."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.fixture
def mock_tools_registry() -> MagicMock:
    """Mock del ToolRegistry con get_schemas y execute."""
    registry = MagicMock()
    registry.get_schemas.return_value = [
        _make_tool_schema("web_search", "Busca en la web"),
        _make_tool_schema("shell_exec", "Ejecuta un comando"),
    ]
    # execute devuelve un objeto ToolResult-like por defecto
    resultado_ok = MagicMock()
    resultado_ok.tool_name = "web_search"
    resultado_ok.output = '{"results": []}'
    resultado_ok.success = True
    resultado_ok.error = None
    registry.execute = AsyncMock(return_value=resultado_ok)
    return registry


@pytest.fixture
def mock_channel_adapter() -> MagicMock:
    """Mock de IChannelOutbound que soporta TEXT, PHOTO y ALBUM."""
    adapter = MagicMock()
    adapter.channel_name = "telegram"
    adapter.capabilities.return_value = {OutboundKind.TEXT, OutboundKind.PHOTO, OutboundKind.ALBUM}
    adapter.send = AsyncMock(return_value=None)
    return adapter


@pytest.fixture
def mock_outbound_registry(mock_channel_adapter: MagicMock) -> MagicMock:
    """Mock del ChannelOutboundRegistry con canal telegram registrado."""
    registry = MagicMock()
    registry.get.return_value = mock_channel_adapter
    registry.list_channels.return_value = ["telegram"]
    return registry


@pytest.fixture
def mock_agent_container(
    mock_tools_registry: MagicMock, mock_outbound_registry: MagicMock
) -> MagicMock:
    """Mock de AgentContainer con _tools y channel_outbound_registry.

    agent_config.channels se configura como dict vacío para que el path de
    broadcast no explote en los tests existentes (emit_assistant=True por default,
    pero emitter=None → broadcasted=False sin error).
    """
    container = MagicMock()
    container._tools = mock_tools_registry
    container.channel_outbound_registry = mock_outbound_registry
    # Evitar que MagicMock devuelva MagicMock en las navegaciones de config
    container.agent_config.channels = {}
    return container


@pytest.fixture
def mock_app_container(mock_agent_container: MagicMock) -> MagicMock:
    """Mock de AppContainer con agente 'foo' registrado y sin broadcast_adapter."""
    app_container = MagicMock()
    app_container.agents = {"foo": mock_agent_container}
    # Sin broadcast_adapter por default → broadcasted=False en los tests existentes
    del app_container.broadcast_adapter
    return app_container


@pytest.fixture
def app(mock_app_container: MagicMock):
    """FastAPI app del admin server para los tests."""
    return create_admin_app(mock_app_container, admin_auth_key="clave-test")


# ---------------------------------------------------------------------------
# GET /admin/tool/list — happy path
# ---------------------------------------------------------------------------


async def test_list_tools_happy_path(app, mock_tools_registry: MagicMock) -> None:
    """GET /admin/tool/list con agente válido → 200 con lista de tools."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/admin/tool/list", params={"agent_id": "foo"}, headers=VALID_KEY)
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    assert len(data["tools"]) == 2
    nombres = [t["name"] for t in data["tools"]]
    assert "web_search" in nombres
    assert "shell_exec" in nombres
    assert data["tools"][0]["description"] == "Busca en la web"
    assert "parameters_schema" in data["tools"][0]


# ---------------------------------------------------------------------------
# GET /admin/tool/list — errores
# ---------------------------------------------------------------------------


async def test_list_tools_agente_desconocido_404(app) -> None:
    """GET /admin/tool/list con agent_id inexistente → 404 agent_not_found."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/admin/tool/list", params={"agent_id": "ghost"}, headers=VALID_KEY)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "agent_not_found"


async def test_list_tools_sin_auth_401(app) -> None:
    """GET /admin/tool/list sin X-Admin-Key → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/admin/tool/list", params={"agent_id": "foo"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /admin/tool/invoke — happy path
# ---------------------------------------------------------------------------


async def test_invoke_tool_happy_path(app, mock_tools_registry: MagicMock) -> None:
    """POST /admin/tool/invoke → 200 con output de la tool."""
    body = {"agent_id": "foo", "tool_name": "web_search", "args": {"query": "Python"}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/tool/invoke", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_name"] == "web_search"
    assert data["success"] is True
    assert data["error"] is None
    mock_tools_registry.execute.assert_awaited_once_with("web_search", query="Python")


# ---------------------------------------------------------------------------
# POST /admin/tool/invoke — tool no registrada → 200 success=False
# ---------------------------------------------------------------------------


async def test_invoke_tool_no_registrada_200_success_false(
    app, mock_tools_registry: MagicMock
) -> None:
    """Tool no registrada → el registry devuelve ToolResult(success=False), el endpoint 200."""
    resultado_fallo = MagicMock()
    resultado_fallo.tool_name = "no_existe"
    resultado_fallo.output = "Tool 'no_existe' no encontrada."
    resultado_fallo.success = False
    resultado_fallo.error = "Tool no registrada: no_existe"
    mock_tools_registry.execute.return_value = resultado_fallo

    body = {"agent_id": "foo", "tool_name": "no_existe", "args": {}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/tool/invoke", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "no registrada" in data["error"]


# ---------------------------------------------------------------------------
# POST /admin/tool/invoke — errores
# ---------------------------------------------------------------------------


async def test_invoke_tool_agente_desconocido_404(app) -> None:
    """POST /admin/tool/invoke con agent_id inexistente → 404."""
    body = {"agent_id": "ghost", "tool_name": "web_search", "args": {}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/tool/invoke", json=body, headers=VALID_KEY)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "agent_not_found"


async def test_invoke_tool_exception_inesperada_500(app, mock_tools_registry: MagicMock) -> None:
    """Si execute() levanta una excepción inesperada → 500 internal_error."""
    mock_tools_registry.execute.side_effect = RuntimeError("error interno inesperado")
    body = {"agent_id": "foo", "tool_name": "web_search", "args": {}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/tool/invoke", json=body, headers=VALID_KEY)
    assert resp.status_code == 500
    assert resp.json()["detail"]["error_code"] == "internal_error"


async def test_invoke_tool_sin_auth_401(app) -> None:
    """POST /admin/tool/invoke sin X-Admin-Key → 401."""
    body = {"agent_id": "foo", "tool_name": "web_search", "args": {}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/tool/invoke", json=body)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /admin/send — happy paths
# ---------------------------------------------------------------------------


async def test_send_text_happy(app, mock_channel_adapter: MagicMock) -> None:
    """POST /admin/send kind=text → 200 y adapter.send() llamado con text."""
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "text",
        "text": "Hola mundo",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent"] is True
    assert data["channel"] == "telegram"
    assert data["kind"] == "text"
    mock_channel_adapter.send.assert_awaited_once()
    call_kwargs = mock_channel_adapter.send.call_args.kwargs
    assert call_kwargs["text"] == "Hola mundo"
    assert call_kwargs["kind"] == OutboundKind.TEXT


async def test_send_photo_happy(app, mock_channel_adapter: MagicMock) -> None:
    """POST /admin/send kind=photo → 200 y adapter.send() con sources como Path."""
    from pathlib import Path

    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "photo",
        "sources": ["/tmp/foto.jpg"],
        "caption": "Mi foto",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    call_kwargs = mock_channel_adapter.send.call_args.kwargs
    assert call_kwargs["sources"] == [Path("/tmp/foto.jpg")]
    assert call_kwargs["caption"] == "Mi foto"
    assert call_kwargs["kind"] == OutboundKind.PHOTO


async def test_send_album_happy(app, mock_channel_adapter: MagicMock) -> None:
    """POST /admin/send kind=album con múltiples sources → 200."""
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "album",
        "sources": ["/tmp/a.jpg", "/tmp/b.jpg"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    assert resp.json()["kind"] == "album"


# ---------------------------------------------------------------------------
# POST /admin/send — errores del adapter
# ---------------------------------------------------------------------------


async def test_send_canal_no_registrado_404(app, mock_outbound_registry: MagicMock) -> None:
    """Canal no registrado en el registry → 404 channel_not_registered."""
    mock_outbound_registry.get.side_effect = KeyError("no hay adapter para 'slack'")
    mock_outbound_registry.list_channels.return_value = ["telegram"]
    body = {
        "agent_id": "foo",
        "channel": "slack",
        "chat_id": "C123",
        "kind": "text",
        "text": "hola",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "channel_not_registered"


async def test_send_kind_no_soportado_422(app, mock_channel_adapter: MagicMock) -> None:
    """Kind válido pero no soportado por el adapter → 422 unsupported_kind."""
    # El adapter solo soporta TEXT, PHOTO, ALBUM — VIDEO no está
    mock_channel_adapter.capabilities.return_value = {OutboundKind.TEXT}
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "video",
        "sources": ["/tmp/video.mp4"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "unsupported_kind"


async def test_send_file_not_found_404(app, mock_channel_adapter: MagicMock) -> None:
    """FileNotFoundError en adapter.send() → 404 source_not_found."""
    mock_channel_adapter.send.side_effect = FileNotFoundError("archivo no existe")
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "photo",
        "sources": ["/tmp/no_existe.jpg"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "source_not_found"


async def test_send_canal_unavailable_503(app, mock_channel_adapter: MagicMock) -> None:
    """RuntimeError en adapter.send() → 503 channel_unavailable."""
    mock_channel_adapter.send.side_effect = RuntimeError("Telegram no está disponible")
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "text",
        "text": "hola",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error_code"] == "channel_unavailable"


# ---------------------------------------------------------------------------
# POST /admin/send — validaciones pydantic
# ---------------------------------------------------------------------------


async def test_send_text_sin_text_422(app) -> None:
    """kind=text sin campo 'text' → 422 (validación pydantic)."""
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "text",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 422


async def test_send_photo_con_dos_sources_422(app) -> None:
    """kind=photo con 2 sources → 422 (requiere exactamente 1)."""
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "photo",
        "sources": ["/tmp/a.jpg", "/tmp/b.jpg"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 422


async def test_send_kind_invalido_422(app) -> None:
    """kind desconocido (ej. 'gif') → 422 (validación pydantic)."""
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "gif",
        "sources": ["/tmp/a.gif"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 422


async def test_send_sin_auth_401(app) -> None:
    """POST /admin/send sin X-Admin-Key → 401."""
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "text",
        "text": "hola",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /admin/send — broadcast
# ---------------------------------------------------------------------------


def _app_con_broadcast_emitter(
    mock_agent_container: MagicMock,
    emit_flag: bool = True,
) -> tuple:
    """Construye una app con broadcast_adapter wired y config de agente apropiada.

    Retorna (app, mock_emitter).
    """
    mock_emitter = AsyncMock()

    mock_agent_container.agent_config.channels = {
        "telegram": {"broadcast": {"emit": {"assistant_response": emit_flag}}}
    }

    app_container = MagicMock()
    app_container.agents = {"foo": mock_agent_container}
    app_container.broadcast_adapter = mock_emitter

    return create_admin_app(app_container, admin_auth_key="clave-test"), mock_emitter


async def test_send_broadcast_text_emite_cuando_todo_ok(
    mock_agent_container: MagicMock,
) -> None:
    """TEXT + broadcast=True + emitter disponible + config flag on → emit() llamado, broadcasted=True."""
    app_b, mock_emitter = _app_con_broadcast_emitter(mock_agent_container, emit_flag=True)
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "text",
        "text": "Hola broadcast",
        "broadcast": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["broadcasted"] is True
    mock_emitter.emit.assert_awaited_once()
    # Verificar que el BroadcastMessage tiene los campos correctos
    msg_arg = mock_emitter.emit.call_args[0][0]
    assert msg_arg.agent_id == "foo"
    assert msg_arg.chat_id == "123456"
    assert msg_arg.event_type == "assistant_response"
    assert msg_arg.content == "Hola broadcast"


async def test_send_broadcast_false_no_emite(
    mock_agent_container: MagicMock,
) -> None:
    """broadcast=False → emit NO llamado, broadcasted=False."""
    app_b, mock_emitter = _app_con_broadcast_emitter(mock_agent_container, emit_flag=True)
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "text",
        "text": "Sin broadcast",
        "broadcast": False,
    }
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    assert resp.json()["broadcasted"] is False
    mock_emitter.emit.assert_not_awaited()


async def test_send_broadcast_kind_photo_no_emite(
    mock_agent_container: MagicMock,
) -> None:
    """kind=photo con broadcast=True → emit NO llamado (solo TEXT dispara broadcast)."""
    app_b, mock_emitter = _app_con_broadcast_emitter(mock_agent_container, emit_flag=True)
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "photo",
        "sources": ["/tmp/foto.jpg"],
        "broadcast": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    assert resp.json()["broadcasted"] is False
    mock_emitter.emit.assert_not_awaited()


async def test_send_broadcast_channel_no_telegram_no_emite(
    mock_agent_container: MagicMock,
    mock_outbound_registry: MagicMock,
) -> None:
    """channel!=telegram con broadcast=True → emit NO llamado."""
    # Registrar un canal "slack" en el registry
    mock_outbound_registry.get.return_value = MagicMock(
        channel_name="slack",
        capabilities=lambda: {OutboundKind.TEXT},
        send=AsyncMock(return_value=None),
    )
    mock_outbound_registry.list_channels.return_value = ["slack"]

    app_b, mock_emitter = _app_con_broadcast_emitter(mock_agent_container, emit_flag=True)
    body = {
        "agent_id": "foo",
        "channel": "slack",
        "chat_id": "C123",
        "kind": "text",
        "text": "Hola Slack",
        "broadcast": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    assert resp.json()["broadcasted"] is False
    mock_emitter.emit.assert_not_awaited()


async def test_send_broadcast_config_flag_off_no_emite(
    mock_agent_container: MagicMock,
) -> None:
    """Config flag assistant_response=False → emit NO llamado aunque broadcast=True."""
    app_b, mock_emitter = _app_con_broadcast_emitter(mock_agent_container, emit_flag=False)
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "text",
        "text": "Flag off",
        "broadcast": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    assert resp.json()["broadcasted"] is False
    mock_emitter.emit.assert_not_awaited()


async def test_send_broadcast_sin_emitter_no_falla(
    mock_agent_container: MagicMock,
) -> None:
    """emitter=None (no wired) → broadcasted=False, sin error, 200 OK."""
    # app_container sin broadcast_adapter
    mock_agent_container.agent_config.channels = {
        "telegram": {"broadcast": {"emit": {"assistant_response": True}}}
    }
    app_container = MagicMock()
    app_container.agents = {"foo": mock_agent_container}
    del app_container.broadcast_adapter

    app_sin_emitter = create_admin_app(app_container, admin_auth_key="clave-test")
    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "text",
        "text": "Sin emitter",
        "broadcast": True,
    }
    async with AsyncClient(
        transport=ASGITransport(app=app_sin_emitter), base_url="http://test"
    ) as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    assert resp.json()["broadcasted"] is False


async def test_send_broadcast_emit_lanza_excepcion_no_propaga(
    mock_agent_container: MagicMock,
) -> None:
    """emit.emit() lanza excepción → endpoint devuelve 200 con broadcasted=False."""
    app_b, mock_emitter = _app_con_broadcast_emitter(mock_agent_container, emit_flag=True)
    mock_emitter.emit.side_effect = RuntimeError("TCP timeout")

    body = {
        "agent_id": "foo",
        "channel": "telegram",
        "chat_id": "123456",
        "kind": "text",
        "text": "Emit falla",
        "broadcast": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://test") as ac:
        resp = await ac.post("/admin/send", json=body, headers=VALID_KEY)
    assert resp.status_code == 200
    assert resp.json()["broadcasted"] is False
