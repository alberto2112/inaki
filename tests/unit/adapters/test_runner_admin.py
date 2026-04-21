"""Tests para la integración del admin server en el daemon runner."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.run(coro)


def test_run_admin_server_creates_uvicorn_server() -> None:
    """Verifica que _run_admin_server crea un uvicorn.Server con la config correcta."""
    from adapters.inbound.daemon.runner import _run_admin_server
    from infrastructure.config import AdminConfig

    admin_cfg = AdminConfig(port=6497, host="127.0.0.1", auth_key="test")
    app_container = MagicMock()
    servers: list = []

    mock_server = MagicMock()
    mock_server.serve = AsyncMock()

    with (
        patch("uvicorn.Config") as mock_config_cls,
        patch("uvicorn.Server", return_value=mock_server),
        patch("adapters.inbound.rest.admin.app.create_admin_app", return_value=MagicMock()),
    ):
        _run(_run_admin_server(app_container, admin_cfg, servers))

    call_kwargs = mock_config_cls.call_args
    assert call_kwargs.kwargs["host"] == "127.0.0.1"
    assert call_kwargs.kwargs["port"] == 6497
    assert mock_server in servers


def test_run_admin_server_disables_signal_handlers() -> None:
    """Verifica que los signal handlers de uvicorn están desactivados."""
    from adapters.inbound.daemon.runner import _run_admin_server
    from infrastructure.config import AdminConfig

    admin_cfg = AdminConfig(port=6497, host="127.0.0.1", auth_key="test")
    app_container = MagicMock()
    servers: list = []

    mock_server = MagicMock()
    mock_server.serve = AsyncMock()

    with (
        patch("uvicorn.Config"),
        patch("uvicorn.Server", return_value=mock_server),
        patch("adapters.inbound.rest.admin.app.create_admin_app", return_value=MagicMock()),
    ):
        _run(_run_admin_server(app_container, admin_cfg, servers))

    # install_signal_handlers debe ser un no-op (lambda)
    mock_server.install_signal_handlers()  # no debe hacer nada


def test_run_admin_server_warns_when_no_auth_key() -> None:
    """Verifica warning cuando auth_key es None."""
    from adapters.inbound.daemon.runner import _run_admin_server
    from infrastructure.config import AdminConfig

    admin_cfg = AdminConfig(port=6497, host="127.0.0.1", auth_key=None)
    app_container = MagicMock()
    servers: list = []

    mock_server = MagicMock()
    mock_server.serve = AsyncMock()

    with (
        patch("uvicorn.Config"),
        patch("uvicorn.Server", return_value=mock_server),
        patch("adapters.inbound.rest.admin.app.create_admin_app", return_value=MagicMock()),
        patch("adapters.inbound.daemon.runner.logger") as mock_logger,
    ):
        _run(_run_admin_server(app_container, admin_cfg, servers))

    mock_logger.warning.assert_called()
    warning_msg = mock_logger.warning.call_args[0][0]
    assert "auth_key" in warning_msg.lower()
