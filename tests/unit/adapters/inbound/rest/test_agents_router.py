"""Tests para adapters/inbound/rest/routers/agents.py — verifica uso de API pública de RunAgent.

Cubre W2 de las correcciones de verify:
  - GET /history usa run_agent.get_history() (API pública), NO _history.load()
  - DELETE /history usa run_agent.clear_history() (API pública), NO _history.clear()

Fix 4 (Judgment Day):
  - Usa create_autospec(RunAgentUseCase) para que accesos a _history/_cfg levanten AttributeError
    en lugar de silenciosamente pasar con MagicMock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adapters.inbound.rest.routers.agents import router
from core.domain.entities.message import Message, Role
from core.use_cases.run_agent import AgentInfoDTO, RunAgentUseCase


@pytest.fixture
def mock_run_agent() -> RunAgentUseCase:
    """Mock de RunAgent con create_autospec — accesos a atributos privados no especificados fallan."""
    agent = create_autospec(RunAgentUseCase, instance=True)
    agent.get_agent_info.return_value = AgentInfoDTO(
        id="dev", name="Inaki", description="Asistente de prueba"
    )
    agent.get_history = AsyncMock(return_value=[])
    agent.clear_history = AsyncMock(return_value=None)
    return agent


@pytest.fixture
def mock_container(mock_run_agent: MagicMock) -> MagicMock:
    """Mock de AgentContainer con run_agent mockeado."""
    container = MagicMock()
    container.run_agent = mock_run_agent
    container.consolidate_memory = MagicMock()
    container.consolidate_memory.execute = AsyncMock(return_value="ok")
    container.scope_registry = MagicMock()
    container.scope_registry.try_mark_busy = AsyncMock(return_value=True)
    container.scope_registry.mark_idle = AsyncMock(return_value=None)
    mock_run_agent.record_user_message = AsyncMock(return_value=None)
    return container


@pytest.fixture
def client(mock_container: MagicMock) -> TestClient:
    """TestClient de FastAPI con el router montado."""
    app = FastAPI()
    app.include_router(router)
    app.state.container = mock_container
    return TestClient(app)


# ---------------------------------------------------------------------------
# W2.1 — GET /history usa run_agent.get_history()
# ---------------------------------------------------------------------------


def test_get_history_usa_api_publica(client: TestClient, mock_run_agent: RunAgentUseCase) -> None:
    """GET /history debe llamar run_agent.get_history() (API pública).

    create_autospec garantiza que cualquier acceso a _history levanta AttributeError
    — la afirmación real es que el endpoint funciona sin acceder a atributos privados.
    """
    response = client.get("/history")

    assert response.status_code == 200
    mock_run_agent.get_history.assert_called_once()  # type: ignore[attr-defined]


def test_get_history_retorna_mensajes(client: TestClient, mock_run_agent: RunAgentUseCase) -> None:
    """GET /history retorna los mensajes del historial en formato correcto."""
    mock_run_agent.get_history.return_value = [  # type: ignore[attr-defined]
        Message(role=Role.USER, content="hola"),
        Message(role=Role.ASSISTANT, content="hola de vuelta"),
    ]

    response = client.get("/history")

    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == "dev"
    assert len(data["messages"]) == 2
    assert data["messages"][0] == {"role": "user", "content": "hola"}
    assert data["messages"][1] == {"role": "assistant", "content": "hola de vuelta"}


# ---------------------------------------------------------------------------
# W2.2 — DELETE /history usa run_agent.clear_history()
# ---------------------------------------------------------------------------


def test_delete_history_usa_api_publica(
    client: TestClient, mock_run_agent: RunAgentUseCase
) -> None:
    """DELETE /history debe llamar run_agent.clear_history() (API pública).

    create_autospec garantiza que cualquier acceso a _history levanta AttributeError
    — la afirmación real es que el endpoint funciona sin acceder a atributos privados.
    """
    response = client.delete("/history")

    assert response.status_code == 200
    mock_run_agent.clear_history.assert_called_once()  # type: ignore[attr-defined]


def test_delete_history_retorna_ok(client: TestClient) -> None:
    """DELETE /history retorna status ok."""
    response = client.delete("/history")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /chat — ChannelContext
# ---------------------------------------------------------------------------


def test_post_chat_sin_channel_usa_rest_y_scope_legacy(
    client: TestClient, mock_run_agent: RunAgentUseCase
) -> None:
    """POST /chat sin channel/chat_id → ctx con channel_type='rest', scope ('', '')."""
    mock_run_agent.execute = AsyncMock(return_value="ok")  # type: ignore[method-assign]

    response = client.post("/chat", json={"message": "hola"})

    assert response.status_code == 200
    kwargs = mock_run_agent.execute.await_args.kwargs  # type: ignore[union-attr]
    assert kwargs["ctx"].channel_type == "rest"
    assert kwargs["ctx"].user_id == "anonymous"
    assert kwargs["channel"] == ""
    assert kwargs["chat_id"] == ""


def test_post_chat_con_channel_y_chat_id(
    client: TestClient, mock_run_agent: RunAgentUseCase
) -> None:
    """POST /chat con channel+chat_id → ctx y scope reales."""
    mock_run_agent.execute = AsyncMock(return_value="ok")  # type: ignore[method-assign]

    response = client.post(
        "/chat", json={"message": "hola", "channel": "telegram", "chat_id": "-100"}
    )

    assert response.status_code == 200
    kwargs = mock_run_agent.execute.await_args.kwargs  # type: ignore[union-attr]
    assert kwargs["ctx"].channel_type == "telegram"
    assert kwargs["ctx"].chat_id == "-100"
    assert kwargs["channel"] == "telegram"
    assert kwargs["chat_id"] == "-100"


def test_post_chat_channel_sin_chat_id_422(client: TestClient) -> None:
    """POST /chat con channel pero sin chat_id → 422 (both-or-none)."""
    response = client.post("/chat", json={"message": "hola", "channel": "telegram"})
    assert response.status_code == 422


def test_post_chat_chat_id_sin_channel_422(client: TestClient) -> None:
    """POST /chat con chat_id pero sin channel → 422 (both-or-none)."""
    response = client.post("/chat", json={"message": "hola", "chat_id": "-100"})
    assert response.status_code == 422
