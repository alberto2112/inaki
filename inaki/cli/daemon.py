"""Comando ``daemon``: arranca el proceso completo (systemd)."""

from __future__ import annotations

import typer

from inaki.app.bootstrap import bootstrap, run_daemon_mode
from inaki.cli import _common


def daemon(
    ctx: typer.Context,
) -> None:
    """Arranca como servicio systemd (levanta todos los canales de todos los agentes)."""
    config_dir, agents_dir = _common.resolve_dirs()
    global_config, registry = bootstrap(config_dir, agents_dir)
    run_daemon_mode(config_dir, agents_dir, global_config, registry)
