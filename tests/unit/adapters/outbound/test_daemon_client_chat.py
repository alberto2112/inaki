"""Tests para DaemonClient — métodos de chat: chat_turn, chat_history, chat_clear.

Cubre tareas 6.1, 6.2, 6.3:
  - chat_turn: serializa body correcto, envía X-Admin-Key, parsea reply
  - ConnectError → DaemonNotRunningError
  - TimeoutException → DaemonTimeoutError
  - HTTP 404 → UnknownAgentError
  - HTTP 401 → DaemonAuthError
  - chat_history: parsea list[{role, content}]
  - chat_clear: DELETE con query param agent_id
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adapters.outbound.daemon_client import DaemonClient
from core.domain.errors import (
    DaemonAuthError,
    DaemonClientError,
    DaemonNotRunningError,
    DaemonTimeoutError,
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
# chat_turn — happy path (tarea 6.1)
# ---------------------------------------------------------------------------


def test_chat_turn_serializa_body_correcto(client: DaemonClient) -> None:
    """chat_turn envía el body JSON correcto con agent_id, session_id y message."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "reply": "Hola",
            "agent_id": "dev",
            "session_id": "sess-1",
        }
        mock_post.return_value = mock_resp
        client.chat_turn("dev", "sess-1", "hola")

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["agent_id"] == "dev"
    assert kwargs["json"]["session_id"] == "sess-1"
    assert kwargs["json"]["message"] == "hola"


def test_chat_turn_envia_x_admin_key(client: DaemonClient) -> None:
    """chat_turn incluye X-Admin-Key en los headers."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"reply": "ok", "agent_id": "dev", "session_id": "s"}
        mock_post.return_value = mock_resp
        client.chat_turn("dev", "s", "test")

    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["X-Admin-Key"] == "test-key"


def test_chat_turn_parsea_reply(client: DaemonClient) -> None:
    """chat_turn retorna el campo 'reply' del JSON de respuesta."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "reply": "Respuesta del agente",
            "agent_id": "dev",
            "session_id": "sess-1",
        }
        mock_post.return_value = mock_resp
        result = client.chat_turn("dev", "sess-1", "hola")

    assert result.reply == "Respuesta del agente"
    assert result.intermediates == []


def test_chat_turn_parsea_intermediates(client: DaemonClient) -> None:
    """chat_turn retorna los bloques intermedios cuando el daemon los incluye."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "reply": "Listo",
            "agent_id": "dev",
            "session_id": "s",
            "intermediates": ["ok, voy a buscar", "tengo los datos"],
        }
        mock_post.return_value = mock_resp
        result = client.chat_turn("dev", "s", "hola")

    assert result.reply == "Listo"
    assert result.intermediates == ["ok, voy a buscar", "tengo los datos"]


def test_chat_turn_usa_chat_timeout(client: DaemonClient) -> None:
    """chat_turn usa el chat_timeout configurado (300s por defecto)."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"reply": "ok", "agent_id": "dev", "session_id": "s"}
        mock_post.return_value = mock_resp
        client.chat_turn("dev", "s", "test")

    _, kwargs = mock_post.call_args
    assert kwargs["timeout"] == 300.0


# ---------------------------------------------------------------------------
# chat_turn — error mapping (tarea 6.2)
# ---------------------------------------------------------------------------


def test_chat_turn_connect_error_raises_daemon_not_running(client: DaemonClient) -> None:
    """ConnectError → DaemonNotRunningError."""
    import httpx

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(DaemonNotRunningError):
            client.chat_turn("dev", "s", "hola")


def test_chat_turn_timeout_raises_daemon_timeout(client: DaemonClient) -> None:
    """TimeoutException → DaemonTimeoutError."""
    import httpx

    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(DaemonTimeoutError):
            client.chat_turn("dev", "s", "hola")


def test_chat_turn_404_raises_unknown_agent(client: DaemonClient) -> None:
    """HTTP 404 → UnknownAgentError con el agent_id correcto."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=404)
        mock_resp.text = '{"detail": "Agent not found"}'
        mock_post.return_value = mock_resp
        with pytest.raises(UnknownAgentError) as exc_info:
            client.chat_turn("dev", "s", "hola")
    assert exc_info.value.agent_id == "dev"


def test_chat_turn_401_raises_auth_error(client: DaemonClient) -> None:
    """HTTP 401 → DaemonAuthError."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=401)
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp
        with pytest.raises(DaemonAuthError):
            client.chat_turn("dev", "s", "hola")


def test_chat_turn_403_raises_auth_error(client: DaemonClient) -> None:
    """HTTP 403 → DaemonAuthError."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=403)
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp
        with pytest.raises(DaemonAuthError):
            client.chat_turn("dev", "s", "hola")


def test_chat_turn_5xx_raises_client_error(client: DaemonClient) -> None:
    """HTTP 5xx → DaemonClientError genérico."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=500)
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp
        with pytest.raises(DaemonClientError) as exc_info:
            client.chat_turn("dev", "s", "hola")
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# task_turn — oneshot ephemeral con scope opcional
# ---------------------------------------------------------------------------


def test_task_turn_serializa_body_minimo(client: DaemonClient) -> None:
    """task_turn sin scope envía body con solo agent_id y message (sin channel/chat_id)."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"reply": "ok", "agent_id": "dev"}
        mock_post.return_value = mock_resp
        client.task_turn("dev", "tarea")

    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["agent_id"] == "dev"
    assert body["message"] == "tarea"
    # Sin scope: las claves channel/chat_id no se envían (o se envían como None)
    assert body.get("channel") is None
    assert body.get("chat_id") is None


def test_task_turn_serializa_scope_completo(client: DaemonClient) -> None:
    """task_turn con channel + chat_id envía ambos en el body JSON."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"reply": "ok", "agent_id": "dev"}
        mock_post.return_value = mock_resp
        client.task_turn("dev", "tarea", channel="telegram", chat_id="-1001582404077")

    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["channel"] == "telegram"
    assert body["chat_id"] == "-1001582404077"


def test_task_turn_url_correcta(client: DaemonClient) -> None:
    """task_turn hace POST a /admin/chat/task."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"reply": "ok", "agent_id": "dev"}
        mock_post.return_value = mock_resp
        client.task_turn("dev", "tarea")

    args, _ = mock_post.call_args
    assert args[0].endswith("/admin/chat/task")


def test_task_turn_parsea_reply_e_intermediates(client: DaemonClient) -> None:
    """task_turn retorna reply + intermediates del JSON de respuesta."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "reply": "Buenos días, panda",
            "agent_id": "anacleto",
            "intermediates": ["pensando..."],
        }
        mock_post.return_value = mock_resp
        result = client.task_turn("anacleto", "saludo del miércoles")

    assert result.reply == "Buenos días, panda"
    assert result.intermediates == ["pensando..."]


# ---------------------------------------------------------------------------
# chat_history (tarea 6.3)
# ---------------------------------------------------------------------------


def test_chat_history_parsea_lista_mensajes(client: DaemonClient) -> None:
    """chat_history retorna list[dict] con role, content y timestamp desde el JSON."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "agent_id": "dev",
            "messages": [
                {"role": "user", "content": "hola", "timestamp": "2026-01-01T12:00:00"},
                {"role": "assistant", "content": "¡hola!", "timestamp": "2026-01-01T12:00:01"},
            ],
        }
        mock_get.return_value = mock_resp
        result = client.chat_history("dev")

    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "hola"
    # Correction 1: timestamp debe estar en la respuesta parseada
    assert "timestamp" in result[0]
    assert result[0]["timestamp"] == "2026-01-01T12:00:00"
    assert result[1]["role"] == "assistant"


def test_chat_history_historia_vacia(client: DaemonClient) -> None:
    """chat_history retorna lista vacía si no hay mensajes."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"agent_id": "dev", "messages": []}
        mock_get.return_value = mock_resp
        result = client.chat_history("dev")

    assert result == []


def test_chat_history_envia_agent_id_como_query_param(client: DaemonClient) -> None:
    """chat_history envía agent_id como query param."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"agent_id": "dev", "messages": []}
        mock_get.return_value = mock_resp
        client.chat_history("dev")

    args, kwargs = mock_get.call_args
    assert args[0].endswith("/admin/chat/history")
    assert kwargs["params"]["agent_id"] == "dev"


def test_chat_history_envia_auth_header(client: DaemonClient) -> None:
    """chat_history incluye X-Admin-Key en los headers."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"agent_id": "dev", "messages": []}
        mock_get.return_value = mock_resp
        client.chat_history("dev")

    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["X-Admin-Key"] == "test-key"


def test_chat_history_404_raises_unknown_agent(client: DaemonClient) -> None:
    """chat_history con 404 → UnknownAgentError."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=404)
        mock_resp.text = "Not Found"
        mock_get.return_value = mock_resp
        with pytest.raises(UnknownAgentError) as exc_info:
            client.chat_history("ghost")
    assert exc_info.value.agent_id == "ghost"


def test_chat_history_connect_error(client: DaemonClient) -> None:
    """chat_history ConnectError → DaemonNotRunningError."""
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(DaemonNotRunningError):
            client.chat_history("dev")


# ---------------------------------------------------------------------------
# chat_clear (tarea 6.3)
# ---------------------------------------------------------------------------


def test_chat_clear_envía_delete_con_agent_id(client: DaemonClient) -> None:
    """chat_clear envía DELETE con agent_id como query param."""
    with patch("httpx.delete") as mock_delete:
        mock_resp = MagicMock(status_code=204)
        mock_delete.return_value = mock_resp
        client.chat_clear("dev")

    args, kwargs = mock_delete.call_args
    assert args[0].endswith("/admin/chat/history")
    assert kwargs["params"]["agent_id"] == "dev"


def test_chat_clear_envia_auth_header(client: DaemonClient) -> None:
    """chat_clear incluye X-Admin-Key en los headers."""
    with patch("httpx.delete") as mock_delete:
        mock_resp = MagicMock(status_code=204)
        mock_delete.return_value = mock_resp
        client.chat_clear("dev")

    _, kwargs = mock_delete.call_args
    assert kwargs["headers"]["X-Admin-Key"] == "test-key"


def test_chat_clear_retorna_none_en_204(client: DaemonClient) -> None:
    """chat_clear retorna None en éxito (204)."""
    with patch("httpx.delete") as mock_delete:
        mock_delete.return_value = MagicMock(status_code=204)
        result = client.chat_clear("dev")

    assert result is None


def test_chat_clear_retorna_none_en_200(client: DaemonClient) -> None:
    """chat_clear retorna None en éxito (200)."""
    with patch("httpx.delete") as mock_delete:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"agent_id": "dev", "cleared": True}
        mock_delete.return_value = mock_resp
        result = client.chat_clear("dev")

    assert result is None


def test_chat_clear_404_raises_unknown_agent(client: DaemonClient) -> None:
    """chat_clear con 404 → UnknownAgentError."""
    with patch("httpx.delete") as mock_delete:
        mock_resp = MagicMock(status_code=404)
        mock_resp.text = "Not Found"
        mock_delete.return_value = mock_resp
        with pytest.raises(UnknownAgentError) as exc_info:
            client.chat_clear("ghost")
    assert exc_info.value.agent_id == "ghost"


def test_chat_clear_connect_error(client: DaemonClient) -> None:
    """chat_clear ConnectError → DaemonNotRunningError."""
    import httpx

    with patch("httpx.delete", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(DaemonNotRunningError):
            client.chat_clear("dev")


def test_chat_clear_401_raises_auth_error(client: DaemonClient) -> None:
    """chat_clear 401 → DaemonAuthError."""
    with patch("httpx.delete") as mock_delete:
        mock_resp = MagicMock(status_code=401)
        mock_resp.text = "Unauthorized"
        mock_delete.return_value = mock_resp
        with pytest.raises(DaemonAuthError):
            client.chat_clear("dev")


# ---------------------------------------------------------------------------
# list_agents (Correction 2)
# ---------------------------------------------------------------------------


def test_list_agents_retorna_lista_de_ids(client: DaemonClient) -> None:
    """list_agents retorna lista de IDs de agentes desde /admin/agents."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"agents": ["dev", "general"]}
        mock_get.return_value = mock_resp
        result = client.list_agents()

    assert result == ["dev", "general"]
    args, _ = mock_get.call_args
    assert args[0].endswith("/admin/agents")


def test_list_agents_connect_error(client: DaemonClient) -> None:
    """list_agents ConnectError → DaemonNotRunningError."""
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(DaemonNotRunningError):
            client.list_agents()


def test_list_agents_401_raises_auth_error(client: DaemonClient) -> None:
    """list_agents 401 → DaemonAuthError."""
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=401)
        mock_resp.text = "Unauthorized"
        mock_get.return_value = mock_resp
        with pytest.raises(DaemonAuthError):
            client.list_agents()


# ---------------------------------------------------------------------------
# Fix 3 (Judgment Day) — DaemonAuthError preserva status_code real
# ---------------------------------------------------------------------------


def test_chat_turn_401_preserva_status_code(client: DaemonClient) -> None:
    """HTTP 401 → DaemonAuthError con status_code=401 (no hardcodeado)."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=401)
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp
        with pytest.raises(DaemonAuthError) as exc_info:
            client.chat_turn("dev", "s", "hola")
    assert exc_info.value.status_code == 401


def test_chat_turn_403_preserva_status_code(client: DaemonClient) -> None:
    """HTTP 403 → DaemonAuthError con status_code=403 (no hardcodeado como 401)."""
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=403)
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp
        with pytest.raises(DaemonAuthError) as exc_info:
            client.chat_turn("dev", "s", "hola")
    assert exc_info.value.status_code == 403
