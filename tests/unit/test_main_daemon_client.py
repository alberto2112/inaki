"""Tests para el routing de CLI via daemon client en main.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from typer.testing import CliRunner

runner = CliRunner()


def _write_minimal_config(tmp_path: Path) -> tuple[Path, Path]:
    """Escribe config mínima y retorna (config_dir, agents_dir)."""
    config_dir = tmp_path / "config"
    agents_dir = tmp_path / "agents"
    config_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)

    (config_dir / "global.yaml").write_text(
        yaml.safe_dump({
            "app": {"name": "Test", "default_agent": "general"},
            "admin": {"port": 6497, "host": "127.0.0.1", "auth_key": "test-key"},
        }),
        encoding="utf-8",
    )
    (agents_dir / "general.yaml").write_text(
        yaml.safe_dump({
            "id": "general",
            "name": "General",
            "description": "Test agent",
            "system_prompt": "You are a test",
            "channels": {"rest": {"port": 6498, "auth_key": "agent-key"}},
        }),
        encoding="utf-8",
    )
    return config_dir, agents_dir


# ---------------------------------------------------------------------------
# _build_daemon_client — helper que construye el client sin AppContainer
# ---------------------------------------------------------------------------


def test_build_daemon_client_returns_client_and_config(tmp_path: Path) -> None:
    config_dir, _ = _write_minimal_config(tmp_path)

    from inaki.cli import _build_daemon_client

    client, global_cfg = _build_daemon_client(config_dir)
    assert client is not None
    assert global_cfg.admin.port == 6497


def test_build_daemon_client_uses_admin_config(tmp_path: Path) -> None:
    config_dir, _ = _write_minimal_config(tmp_path)

    from inaki.cli import _build_daemon_client

    client, _ = _build_daemon_client(config_dir)
    from adapters.outbound.daemon_client import DaemonClient

    assert isinstance(client, DaemonClient)


# ---------------------------------------------------------------------------
# Chat sin daemon → error claro
# ---------------------------------------------------------------------------


def test_chat_without_daemon_shows_error(tmp_path: Path) -> None:
    """Si el daemon no corre, chat muestra error."""
    config_dir, agents_dir = _write_minimal_config(tmp_path)

    from inaki.cli import app

    with patch("inaki.cli._resolve_dirs", return_value=(config_dir, agents_dir)):
        with patch("inaki.cli._build_daemon_client") as mock_build:
            mock_client = MagicMock()
            mock_client.health.return_value = False
            mock_build.return_value = (mock_client, MagicMock(app=MagicMock(default_agent="general")))

            result = runner.invoke(app, ["chat", "--agent", "general"])

    assert result.exit_code != 0
    assert "daemon" in result.output.lower()
