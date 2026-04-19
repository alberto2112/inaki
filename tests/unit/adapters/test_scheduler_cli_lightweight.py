"""Tests para el bootstrap liviano del scheduler CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml


def _write_minimal_config(config_dir: Path) -> None:
    """Escribe un global.yaml mínimo para tests."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "global.yaml").write_text(
        yaml.safe_dump({
            "app": {"name": "Test"},
            "scheduler": {"db_filename": ":memory:"},
        }),
        encoding="utf-8",
    )


def test_lightweight_bootstrap_does_not_import_app_container(tmp_path: Path) -> None:
    """Verificar que el bootstrap liviano NO importa AppContainer."""
    config_dir = tmp_path / "config"
    _write_minimal_config(config_dir)

    from adapters.inbound.cli.scheduler_cli import _bootstrap_uc

    ctx = MagicMock()
    ctx.obj = {"config_dir": config_dir}

    with patch(
        "adapters.inbound.cli.scheduler_cli._create_lightweight_uc"
    ) as mock_create:
        mock_uc = MagicMock()
        mock_create.return_value = (mock_uc, MagicMock())
        _bootstrap_uc(ctx)

    # No debe haber importado AppContainer durante el bootstrap
    mock_create.assert_called_once()


def test_lightweight_bootstrap_returns_use_case(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_minimal_config(config_dir)

    from adapters.inbound.cli.scheduler_cli import _bootstrap_uc

    ctx = MagicMock()
    ctx.obj = {"config_dir": config_dir}

    with patch(
        "adapters.inbound.cli.scheduler_cli._create_lightweight_uc"
    ) as mock_create:
        mock_uc = MagicMock()
        mock_create.return_value = (mock_uc, MagicMock())
        result = _bootstrap_uc(ctx)

    assert result is mock_uc


def test_reload_callback_silences_all_exceptions(tmp_path: Path) -> None:
    """El callback de reload no debe propagar ninguna excepción."""
    from adapters.inbound.cli.scheduler_cli import _notify_daemon_reload

    # Simula que el daemon no está corriendo
    with patch("adapters.outbound.daemon_client.DaemonClient") as MockClient:
        instance = MockClient.return_value
        instance.scheduler_reload.side_effect = Exception("connection refused")
        # No debe levantar excepción
        _notify_daemon_reload("http://127.0.0.1:6497", None)


def test_reload_callback_calls_scheduler_reload(tmp_path: Path) -> None:
    from adapters.inbound.cli.scheduler_cli import _notify_daemon_reload

    with patch("adapters.outbound.daemon_client.DaemonClient") as MockClient:
        instance = MockClient.return_value
        instance.scheduler_reload.return_value = True
        _notify_daemon_reload("http://127.0.0.1:6497", "my-key")

    MockClient.assert_called_once_with(
        admin_base_url="http://127.0.0.1:6497", auth_key="my-key"
    )
    instance.scheduler_reload.assert_called_once()


def test_reload_callback_silences_connect_error() -> None:
    from adapters.inbound.cli.scheduler_cli import _notify_daemon_reload
    from core.domain.errors import DaemonNotRunningError

    with patch("adapters.outbound.daemon_client.DaemonClient") as MockClient:
        instance = MockClient.return_value
        instance.scheduler_reload.side_effect = DaemonNotRunningError()
        # No debe levantar
        _notify_daemon_reload("http://127.0.0.1:6497", None)
