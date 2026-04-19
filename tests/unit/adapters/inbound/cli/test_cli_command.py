"""Tests para el comando `chat` en inaki/cli.py.

Cubre tareas 9.1, 9.2:
  9.1 — `chat` command con daemon mock; NO instancia AppContainer;
        DaemonClient recibe agent_id correcto.
  9.2 — daemon inalcanzable al startup → mensaje accionable y salida no-zero.

Spec cli-chat-client/spec.md:
  - UUID generated per process
  - Daemon unreachable at startup
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(health_ok: bool = True) -> MagicMock:
    """Construye un DaemonClient mock con health controlable."""
    client = MagicMock()
    client.health.return_value = health_ok
    from core.domain.value_objects.chat_turn_result import ChatTurnResult
    client.chat_turn.return_value = ChatTurnResult(reply="resp")
    return client


# ---------------------------------------------------------------------------
# 9.1 — chat command NO instancia AppContainer (tarea 9.1)
# ---------------------------------------------------------------------------


def test_chat_command_no_instancia_app_container() -> None:
    """El path `chat` NO debe instanciar AppContainer."""
    from inaki.cli import app

    runner = CliRunner()

    mock_client = _make_mock_client(health_ok=True)

    with patch("inaki.cli._build_daemon_client", return_value=(mock_client, MagicMock(app=MagicMock(default_agent="dev")))):
        with patch("adapters.inbound.cli.cli_runner.run_cli"):
            with patch("infrastructure.container.AppContainer") as mock_app_container:
                runner.invoke(app, ["chat", "--agent", "dev"])
                mock_app_container.assert_not_called(), "AppContainer fue instanciado en el path chat — violación del diseño"


def test_chat_command_pasa_agent_id_al_runner() -> None:
    """El comando `chat --agent dev` pasa 'dev' al runner."""
    from inaki.cli import app

    runner = CliRunner()
    mock_client = _make_mock_client(health_ok=True)
    mock_global_config = MagicMock()
    mock_global_config.app.default_agent = "default"

    with patch("inaki.cli._build_daemon_client", return_value=(mock_client, mock_global_config)):
        with patch("adapters.inbound.cli.cli_runner.run_cli") as mock_run_cli:
            runner.invoke(app, ["chat", "--agent", "dev"])

    mock_run_cli.assert_called_once()
    args = mock_run_cli.call_args[0]
    # args: (client, agent_id)
    assert args[1] == "dev"


def test_chat_command_usa_default_agent_si_no_se_pasa_flag() -> None:
    """Sin --agent, el comando usa global_config.app.default_agent."""
    from inaki.cli import app

    runner = CliRunner()
    mock_client = _make_mock_client(health_ok=True)
    mock_global_config = MagicMock()
    mock_global_config.app.default_agent = "general"

    with patch("inaki.cli._build_daemon_client", return_value=(mock_client, mock_global_config)):
        with patch("adapters.inbound.cli.cli_runner.run_cli") as mock_run_cli:
            runner.invoke(app, ["chat"])

    mock_run_cli.assert_called_once()
    args = mock_run_cli.call_args[0]
    assert args[1] == "general"


def test_chat_command_pasa_client_al_runner() -> None:
    """El comando `chat` pasa el DaemonClient construido al runner."""
    from inaki.cli import app

    runner = CliRunner()
    mock_client = _make_mock_client(health_ok=True)
    mock_global_config = MagicMock()
    mock_global_config.app.default_agent = "dev"

    with patch("inaki.cli._build_daemon_client", return_value=(mock_client, mock_global_config)):
        with patch("adapters.inbound.cli.cli_runner.run_cli") as mock_run_cli:
            runner.invoke(app, ["chat", "--agent", "dev"])

    args = mock_run_cli.call_args[0]
    assert args[0] is mock_client


# ---------------------------------------------------------------------------
# 9.2 — daemon inalcanzable al startup (tarea 9.2)
# ---------------------------------------------------------------------------


def test_chat_daemon_inalcanzable_sale_no_zero() -> None:
    """Daemon inalcanzable → salida con código no-zero."""
    from inaki.cli import app

    runner = CliRunner()
    mock_client = _make_mock_client(health_ok=False)
    mock_global_config = MagicMock()

    with patch("inaki.cli._build_daemon_client", return_value=(mock_client, mock_global_config)):
        with patch("adapters.inbound.cli.cli_runner.run_cli") as mock_run_cli:
            result = runner.invoke(app, ["chat"])

    assert result.exit_code != 0
    # run_cli no debe haber sido llamado
    mock_run_cli.assert_not_called()


def test_chat_daemon_inalcanzable_imprime_mensaje_accionable() -> None:
    """Daemon inalcanzable → imprime mensaje con instrucción para `inaki daemon`."""
    from inaki.cli import app

    runner = CliRunner()
    mock_client = _make_mock_client(health_ok=False)
    mock_global_config = MagicMock()

    with patch("inaki.cli._build_daemon_client", return_value=(mock_client, mock_global_config)):
        result = runner.invoke(app, ["chat"])

    output = result.output + (result.stdout if hasattr(result, "stdout") else "")
    assert "daemon" in output.lower() or "inaki daemon" in output.lower()
