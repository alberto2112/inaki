"""Tests para el REPL sync de run_cli — comportamiento de sesión y client.

Tras el cambio cli-chat-via-rest, run_cli ya no gestiona ChannelContext directamente
(eso lo hace el router REST del daemon). Aquí verificamos que:
  - El runner usa un session_id UUID consistente dentro de una misma sesión.
  - El runner delega correctamente al IDaemonClient.
  - El runner sale limpiamente ante KeyboardInterrupt y EOFError.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from core.domain.errors import DaemonNotRunningError
from core.domain.value_objects.chat_turn_result import ChatTurnResult


@pytest.fixture
def mock_client():
    """IDaemonClient mockeado."""
    client = MagicMock()
    client.chat_turn.return_value = ChatTurnResult(reply="respuesta")
    client.chat_clear.return_value = None
    client.chat_history.return_value = []
    return client


def test_run_cli_usa_mismo_session_id_en_todos_los_turnos(mock_client) -> None:
    """Todos los turnos de una misma sesión usan el mismo session_id UUID."""
    from adapters.inbound.cli.cli_runner import run_cli

    session_ids_capturados: list[str] = []

    def capturar(agent_id, session_id, mensaje):
        session_ids_capturados.append(session_id)
        return ChatTurnResult(reply="resp")

    mock_client.chat_turn.side_effect = capturar

    with patch("builtins.input", side_effect=["hola", "mundo", "/exit"]):
        run_cli(mock_client, "default")

    assert len(session_ids_capturados) == 2
    # Ambos turnos de la MISMA sesión deben tener el MISMO session_id
    assert session_ids_capturados[0] == session_ids_capturados[1]
    # Y debe ser un UUID válido
    uuid.UUID(session_ids_capturados[0])


def test_run_cli_delega_mensajes_al_client(mock_client) -> None:
    """run_cli llama a client.chat_turn por cada mensaje del usuario."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=["mensaje de prueba", "/exit"]):
        run_cli(mock_client, "default")

    assert mock_client.chat_turn.call_count == 1
    args = mock_client.chat_turn.call_args[0]
    assert args[0] == "default"        # agent_id
    assert args[2] == "mensaje de prueba"  # mensaje


def test_run_cli_sale_limpiamente_ante_keyboard_interrupt(mock_client) -> None:
    """KeyboardInterrupt no propaga excepción al caller."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=KeyboardInterrupt()):
        run_cli(mock_client, "default")  # No debe lanzar


def test_run_cli_sale_limpiamente_ante_eof(mock_client) -> None:
    """EOFError (pipe cerrado) no propaga excepción al caller."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=EOFError()):
        run_cli(mock_client, "default")  # No debe lanzar
