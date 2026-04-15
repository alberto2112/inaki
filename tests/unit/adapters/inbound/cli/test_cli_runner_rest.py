"""Tests para cli_runner.py — REPL sync sobre IDaemonClient.

Cubre tareas 8.1–8.6 (TEST):
  8.1 — run_cli genera UUID único por llamada
  8.2 — /exit y /quit terminan el loop sin llamar al client
  8.3 — /clear llama client.chat_clear(agent_id) e imprime "Historial limpiado."
  8.4 — Mensaje normal llama client.chat_turn y muestra la respuesta
  8.5 — DaemonNotRunningError → sale del loop; DaemonTimeoutError → imprime y continúa
  8.6 — KeyboardInterrupt → salida limpia código 0

Spec cli-chat-client/spec.md:
  - UUID generated per process
  - /exit or /quit
  - /clear
  - Happy path — user sends message
  - Daemon becomes unreachable mid-session
  - User presses Ctrl+C
"""

from __future__ import annotations

import uuid
from io import StringIO
from unittest.mock import call, create_autospec, patch

import pytest

from core.domain.errors import DaemonNotRunningError, DaemonTimeoutError
from core.ports.outbound.daemon_client_port import IDaemonClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client() -> IDaemonClient:
    """IDaemonClient mockeado con autospec — las assertions son reales."""
    client = create_autospec(IDaemonClient, instance=True)
    client.chat_turn.return_value = "Respuesta del agente"
    client.chat_clear.return_value = None
    client.chat_history.return_value = []
    return client


def _run_cli(mock_client, inputs: list[str], agent_id: str = "dev") -> tuple[str, str]:
    """Helper: ejecuta run_cli con stdin simulado; retorna (stdout, stderr)."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=inputs + [EOFError()]):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            try:
                run_cli(mock_client, agent_id)
            except SystemExit:
                pass
    return mock_stdout.getvalue()


# ---------------------------------------------------------------------------
# 8.1 — UUID único por llamada (tarea 8.1)
# ---------------------------------------------------------------------------


def test_run_cli_genera_uuid_unico_por_llamada(mock_client: MagicMock) -> None:
    """Cada invocación de run_cli genera un session_id UUID distinto."""
    from adapters.inbound.cli.cli_runner import run_cli

    capturados: list[str] = []

    def capturar_session_id(agent_id, session_id, mensaje):
        capturados.append(session_id)
        return "resp"

    mock_client.chat_turn.side_effect = capturar_session_id

    with patch("builtins.input", side_effect=["hola", "/exit"]):
        run_cli(mock_client, "dev")

    with patch("builtins.input", side_effect=["hola2", "/exit"]):
        run_cli(mock_client, "dev")

    assert len(capturados) == 2
    # Ambos son UUIDs válidos
    for s in capturados:
        uuid.UUID(s)  # lanza ValueError si no es UUID válido
    # Son distintos
    assert capturados[0] != capturados[1]


# ---------------------------------------------------------------------------
# 8.2 — /exit y /quit terminan el loop (tarea 8.2)
# ---------------------------------------------------------------------------


def test_exit_termina_loop_sin_llamar_al_client(mock_client: MagicMock) -> None:
    """/exit termina el loop sin llamar a chat_turn."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=["/exit"]):
        run_cli(mock_client, "dev")

    mock_client.chat_turn.assert_not_called()


def test_quit_termina_loop_sin_llamar_al_client(mock_client: MagicMock) -> None:
    """/quit termina el loop sin llamar a chat_turn."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=["/quit"]):
        run_cli(mock_client, "dev")

    mock_client.chat_turn.assert_not_called()


def test_eof_termina_loop_sin_llamar_al_client(mock_client: MagicMock) -> None:
    """EOFError (pipe cerrado) termina el loop limpiamente."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=[EOFError()]):
        run_cli(mock_client, "dev")

    mock_client.chat_turn.assert_not_called()


# ---------------------------------------------------------------------------
# 8.3 — /clear (tarea 8.3)
# ---------------------------------------------------------------------------


def test_clear_llama_chat_clear(mock_client: MagicMock) -> None:
    """/clear llama client.chat_clear con el agent_id correcto."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=["/clear", "/exit"]):
        run_cli(mock_client, "dev")

    mock_client.chat_clear.assert_called_once_with("dev")


def test_clear_imprime_confirmacion(mock_client: MagicMock, capsys) -> None:
    """/clear imprime 'Historial limpiado.' tras ejecutar la operación."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=["/clear", "/exit"]):
        run_cli(mock_client, "dev")

    captured = capsys.readouterr()
    assert "Historial limpiado." in captured.out


# ---------------------------------------------------------------------------
# 8.4 — Mensaje normal (tarea 8.4)
# ---------------------------------------------------------------------------


def test_mensaje_normal_llama_chat_turn(mock_client: MagicMock) -> None:
    """Mensaje normal llama client.chat_turn con agent_id, session_id y texto."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=["hola daemon", "/exit"]):
        run_cli(mock_client, "dev")

    assert mock_client.chat_turn.call_count == 1
    args = mock_client.chat_turn.call_args[0]
    assert args[0] == "dev"       # agent_id
    uuid.UUID(args[1])            # session_id es UUID válido
    assert args[2] == "hola daemon"  # mensaje


def test_mensaje_normal_imprime_respuesta(mock_client: MagicMock, capsys) -> None:
    """Mensaje normal imprime la respuesta del agente."""
    from adapters.inbound.cli.cli_runner import run_cli

    mock_client.chat_turn.return_value = "Respuesta de prueba"

    with patch("builtins.input", side_effect=["hola", "/exit"]):
        run_cli(mock_client, "dev")

    captured = capsys.readouterr()
    assert "Respuesta de prueba" in captured.out


def test_input_vacio_no_llama_al_client(mock_client: MagicMock) -> None:
    """Línea en blanco no llama a chat_turn."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=["", "   ", "/exit"]):
        run_cli(mock_client, "dev")

    mock_client.chat_turn.assert_not_called()


# ---------------------------------------------------------------------------
# 8.5 — DaemonNotRunningError y DaemonTimeoutError (tarea 8.5)
# ---------------------------------------------------------------------------


def test_daemon_not_running_termina_loop(mock_client: MagicMock, capsys) -> None:
    """DaemonNotRunningError en turno → imprime error y sale del loop (fatal)."""
    from adapters.inbound.cli.cli_runner import run_cli

    mock_client.chat_turn.side_effect = DaemonNotRunningError()

    with patch("builtins.input", side_effect=["hola", "segunda"]):
        run_cli(mock_client, "dev")

    # Solo un intento — el loop debe haber terminado
    assert mock_client.chat_turn.call_count == 1
    # Mensaje de error impreso
    captured = capsys.readouterr()
    assert "daemon" in captured.out.lower() or "corriendo" in captured.out.lower()


def test_daemon_timeout_continua_loop(mock_client: MagicMock, capsys) -> None:
    """DaemonTimeoutError en turno → imprime mensaje y continúa el loop (transient)."""
    from adapters.inbound.cli.cli_runner import run_cli

    # Primer turno: timeout. Segundo turno: éxito.
    mock_client.chat_turn.side_effect = [DaemonTimeoutError(), "ok"]

    with patch("builtins.input", side_effect=["primer mensaje", "segundo mensaje", "/exit"]):
        run_cli(mock_client, "dev")

    # Dos intentos — el loop continúa
    assert mock_client.chat_turn.call_count == 2
    captured = capsys.readouterr()
    assert "timeout" in captured.out.lower() or "tiempo" in captured.out.lower()


# ---------------------------------------------------------------------------
# 8.6 — KeyboardInterrupt (tarea 8.6)
# ---------------------------------------------------------------------------


def test_keyboard_interrupt_sale_limpiamente(mock_client: MagicMock) -> None:
    """KeyboardInterrupt (Ctrl+C) → salida limpia sin excepción ni traceback."""
    from adapters.inbound.cli.cli_runner import run_cli

    with patch("builtins.input", side_effect=KeyboardInterrupt()):
        # No debe lanzar ninguna excepción al exterior
        run_cli(mock_client, "dev")

    mock_client.chat_turn.assert_not_called()


def test_keyboard_interrupt_durante_turno_sale_limpiamente(mock_client: MagicMock) -> None:
    """KeyboardInterrupt mientras se espera respuesta → salida limpia."""
    from adapters.inbound.cli.cli_runner import run_cli

    mock_client.chat_turn.side_effect = KeyboardInterrupt()

    with patch("builtins.input", side_effect=["hola"]):
        # No debe lanzar ninguna excepción al exterior
        run_cli(mock_client, "dev")


# ---------------------------------------------------------------------------
# Correction 2 — /agents llama client.list_agents() y muestra la lista
# ---------------------------------------------------------------------------


def test_agents_llama_list_agents_y_muestra_resultado(
    mock_client: MagicMock, capsys
) -> None:
    """/agents llama client.list_agents() y muestra los agentes disponibles."""
    from adapters.inbound.cli.cli_runner import run_cli

    mock_client.list_agents.return_value = ["dev", "general"]

    with patch("builtins.input", side_effect=["/agents", "/exit"]):
        run_cli(mock_client, "dev")

    mock_client.list_agents.assert_called_once()
    captured = capsys.readouterr()
    assert "dev" in captured.out
    assert "general" in captured.out


def test_agents_maneja_error_de_conexion(mock_client: MagicMock, capsys) -> None:
    """/agents con daemon no disponible → imprime error, el loop continúa y procesa el siguiente input."""
    from adapters.inbound.cli.cli_runner import run_cli
    from core.domain.errors import DaemonNotRunningError

    mock_client.list_agents.side_effect = DaemonNotRunningError()
    mock_client.chat_turn.return_value = "respuesta post-error"

    # Secuencia: /agents (falla) → mensaje normal → /exit
    with patch("builtins.input", side_effect=["/agents", "hola después del error", "/exit"]):
        run_cli(mock_client, "dev")

    captured = capsys.readouterr()
    # El error debe haberse impreso
    assert captured.out  # algún output de error

    # CRÍTICO: el loop debe haber continuado y procesado el siguiente input
    mock_client.chat_turn.assert_called_once()
    args = mock_client.chat_turn.call_args[0]
    assert args[2] == "hola después del error"
