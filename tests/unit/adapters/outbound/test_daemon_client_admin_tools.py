"""Tests para DaemonClient — métodos de tools y send (Fase 5).

Cubre:
  list_tools:
    - Happy path: GET a /admin/tool/list con agent_id como query param
    - Envía X-Admin-Key
    - 404 → UnknownAgentError
    - 401 → DaemonAuthError
    - ConnectError → DaemonNotRunningError

  invoke_tool:
    - Happy path: POST a /admin/tool/invoke con body correcto
    - Envía X-Admin-Key y usa chat_timeout
    - Retorna dict completo (success, output, error)
    - 404 → UnknownAgentError
    - 401 → DaemonAuthError
    - ConnectError → DaemonNotRunningError

  send_message_via:
    - Happy path TEXT: body incluye solo los campos no-None
    - Happy path PHOTO: body incluye sources
    - Campos None no se incluyen en el body
    - Envía X-Admin-Key
    - 404 → UnknownAgentError
    - 401 → DaemonAuthError
    - ConnectError → DaemonNotRunningError
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adapters.outbound.daemon_client import DaemonClient
from core.domain.errors import (
    DaemonAuthError,
    DaemonNotRunningError,
    UnknownAgentError,
)


@pytest.fixture
def client() -> DaemonClient:
    return DaemonClient(
        admin_base_url="http://127.0.0.1:6497",
        auth_key="test-key",
        chat_timeout=300.0,
    )


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------


def test_list_tools_happy_path(client: DaemonClient) -> None:
    """list_tools hace GET a /admin/tool/list con agent_id como query param."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "tools": [
                {
                    "name": "web_search",
                    "description": "Busca en la web",
                    "parameters_schema": {},
                }
            ]
        }
        mock_get.return_value = mock_resp
        result = client.list_tools("dev")

    assert "tools" in result
    assert result["tools"][0]["name"] == "web_search"
    args, kwargs = mock_get.call_args
    assert args[0].endswith("/admin/tool/list")
    assert kwargs["params"]["agent_id"] == "dev"


def test_list_tools_envia_auth_header(client: DaemonClient) -> None:
    """list_tools incluye X-Admin-Key en headers."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"tools": []}
        mock_get.return_value = mock_resp
        client.list_tools("dev")

    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["X-Admin-Key"] == "test-key"


def test_list_tools_404_raises_unknown_agent(client: DaemonClient) -> None:
    """list_tools con 404 → UnknownAgentError."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=404)
        mock_resp.text = "Not Found"
        mock_get.return_value = mock_resp
        with pytest.raises(UnknownAgentError) as exc_info:
            client.list_tools("ghost")
    assert exc_info.value.agent_id == "ghost"


def test_list_tools_401_raises_auth_error(client: DaemonClient) -> None:
    """list_tools con 401 → DaemonAuthError."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=401)
        mock_resp.text = "Unauthorized"
        mock_get.return_value = mock_resp
        with pytest.raises(DaemonAuthError):
            client.list_tools("dev")


def test_list_tools_connect_error(client: DaemonClient) -> None:
    """list_tools ConnectError → DaemonNotRunningError."""
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(DaemonNotRunningError):
            client.list_tools("dev")


# ---------------------------------------------------------------------------
# invoke_tool
# ---------------------------------------------------------------------------


def test_invoke_tool_happy_path(client: DaemonClient) -> None:
    """invoke_tool hace POST a /admin/tool/invoke con el body correcto."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "tool_name": "web_search",
            "output": '{"results": []}',
            "success": True,
            "error": None,
        }
        mock_post.return_value = mock_resp
        result = client.invoke_tool("dev", "web_search", {"query": "Python"})

    assert result["tool_name"] == "web_search"
    assert result["success"] is True
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["agent_id"] == "dev"
    assert kwargs["json"]["tool_name"] == "web_search"
    assert kwargs["json"]["args"] == {"query": "Python"}


def test_invoke_tool_body_correcto_sin_args(client: DaemonClient) -> None:
    """invoke_tool sin args envía args={} en el body."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "tool_name": "shell_exec",
            "output": "ok",
            "success": True,
            "error": None,
        }
        mock_post.return_value = mock_resp
        client.invoke_tool("dev", "shell_exec")

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["args"] == {}


def test_invoke_tool_usa_chat_timeout(client: DaemonClient) -> None:
    """invoke_tool usa chat_timeout (tools pueden tardar)."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "tool_name": "t",
            "output": "ok",
            "success": True,
            "error": None,
        }
        mock_post.return_value = mock_resp
        client.invoke_tool("dev", "t")

    _, kwargs = mock_post.call_args
    assert kwargs["timeout"] == 300.0


def test_invoke_tool_envia_auth_header(client: DaemonClient) -> None:
    """invoke_tool incluye X-Admin-Key en headers."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "tool_name": "t",
            "output": "ok",
            "success": True,
            "error": None,
        }
        mock_post.return_value = mock_resp
        client.invoke_tool("dev", "t")

    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["X-Admin-Key"] == "test-key"


def test_invoke_tool_retorna_success_false(client: DaemonClient) -> None:
    """invoke_tool retorna success=False cuando la tool no existe (HTTP 200)."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "tool_name": "no_existe",
            "output": "Tool no registrada",
            "success": False,
            "error": "Tool no registrada: no_existe",
        }
        mock_post.return_value = mock_resp
        result = client.invoke_tool("dev", "no_existe")

    assert result["success"] is False
    assert "no registrada" in result["error"]


def test_invoke_tool_404_raises_unknown_agent(client: DaemonClient) -> None:
    """invoke_tool con 404 → UnknownAgentError."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=404)
        mock_resp.text = "Not Found"
        mock_post.return_value = mock_resp
        with pytest.raises(UnknownAgentError) as exc_info:
            client.invoke_tool("ghost", "web_search")
    assert exc_info.value.agent_id == "ghost"


def test_invoke_tool_401_raises_auth_error(client: DaemonClient) -> None:
    """invoke_tool con 401 → DaemonAuthError."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=401)
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp
        with pytest.raises(DaemonAuthError):
            client.invoke_tool("dev", "web_search")


def test_invoke_tool_connect_error(client: DaemonClient) -> None:
    """invoke_tool ConnectError → DaemonNotRunningError."""
    import httpx

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(DaemonNotRunningError):
            client.invoke_tool("dev", "web_search")


# ---------------------------------------------------------------------------
# send_message_via
# ---------------------------------------------------------------------------


def test_send_message_via_text_happy(client: DaemonClient) -> None:
    """send_message_via kind=text incluye solo los campos no-None en el body."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "sent": True,
            "channel": "telegram",
            "chat_id": "123456",
            "kind": "text",
        }
        mock_post.return_value = mock_resp
        result = client.send_message_via(
            agent_id="dev",
            channel="telegram",
            chat_id="123456",
            kind="text",
            text="Hola mundo",
        )

    assert result["sent"] is True
    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["agent_id"] == "dev"
    assert body["channel"] == "telegram"
    assert body["chat_id"] == "123456"
    assert body["kind"] == "text"
    assert body["text"] == "Hola mundo"
    # sources y caption no se incluyen si son None
    assert "sources" not in body
    assert "caption" not in body


def test_send_message_via_photo_incluye_sources(client: DaemonClient) -> None:
    """send_message_via kind=photo incluye sources en el body."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "sent": True,
            "channel": "telegram",
            "chat_id": "123456",
            "kind": "photo",
        }
        mock_post.return_value = mock_resp
        client.send_message_via(
            agent_id="dev",
            channel="telegram",
            chat_id="123456",
            kind="photo",
            sources=["/tmp/foto.jpg"],
            caption="Mi foto",
        )

    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["sources"] == ["/tmp/foto.jpg"]
    assert body["caption"] == "Mi foto"


def test_send_message_via_campos_none_no_se_incluyen(client: DaemonClient) -> None:
    """Los campos None (sources, caption, text) no aparecen en el body enviado."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "sent": True,
            "channel": "telegram",
            "chat_id": "123456",
            "kind": "text",
        }
        mock_post.return_value = mock_resp
        client.send_message_via(
            agent_id="dev",
            channel="telegram",
            chat_id="123456",
            kind="text",
            text="solo texto",
            sources=None,
            caption=None,
        )

    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert "sources" not in body
    assert "caption" not in body


def test_send_message_via_url_correcta(client: DaemonClient) -> None:
    """send_message_via hace POST a /admin/send."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "sent": True,
            "channel": "telegram",
            "chat_id": "123456",
            "kind": "text",
        }
        mock_post.return_value = mock_resp
        client.send_message_via("dev", "telegram", "123456", "text", text="hola")

    args, _ = mock_post.call_args
    assert args[0].endswith("/admin/send")


def test_send_message_via_envia_auth_header(client: DaemonClient) -> None:
    """send_message_via incluye X-Admin-Key en headers."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "sent": True,
            "channel": "telegram",
            "chat_id": "123456",
            "kind": "text",
        }
        mock_post.return_value = mock_resp
        client.send_message_via("dev", "telegram", "123456", "text", text="hola")

    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["X-Admin-Key"] == "test-key"


def test_send_message_via_404_raises_unknown_agent(client: DaemonClient) -> None:
    """send_message_via con 404 → UnknownAgentError."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=404)
        mock_resp.text = "Not Found"
        mock_post.return_value = mock_resp
        with pytest.raises(UnknownAgentError) as exc_info:
            client.send_message_via("ghost", "telegram", "123456", "text", text="hola")
    assert exc_info.value.agent_id == "ghost"


def test_send_message_via_401_raises_auth_error(client: DaemonClient) -> None:
    """send_message_via con 401 → DaemonAuthError."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=401)
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp
        with pytest.raises(DaemonAuthError):
            client.send_message_via("dev", "telegram", "123456", "text", text="hola")


def test_send_message_via_connect_error(client: DaemonClient) -> None:
    """send_message_via ConnectError → DaemonNotRunningError."""
    import httpx

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(DaemonNotRunningError):
            client.send_message_via("dev", "telegram", "123456", "text", text="hola")
