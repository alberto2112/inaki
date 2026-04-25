"""
Sub-app Typer para el comando ``inaki setup``.

Comandos disponibles:

  ``inaki setup``           → abre la TUI (alias de ``tui``)
  ``inaki setup tui``       → abre la TUI Textual de configuración offline
  ``inaki setup secret-key`` → lanza el wizard Fernet (legacy, solo INAKI_SECRET_KEY)
  ``inaki setup webui``     → placeholder (no implementado todavía)
"""

from __future__ import annotations

import typer

setup_app = typer.Typer(
    name="setup",
    help="Configuración del sistema. Sin subcomando → abre la TUI interactiva.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@setup_app.callback()
def _setup_default(ctx: typer.Context) -> None:
    """Sin subcomando → abre la TUI (equivalente a ``inaki setup tui``)."""
    if ctx.invoked_subcommand is None:
        _lanzar_tui()


@setup_app.command("tui")
def tui() -> None:
    """Abre la TUI interactiva de configuración (offline — no requiere daemon)."""
    _lanzar_tui()


@setup_app.command("secret-key")
def secret_key() -> None:
    """Wizard de clave Fernet (INAKI_SECRET_KEY). Legacy — solo gestiona secrets del .env."""
    from adapters.inbound.cli.setup_wizard import run_setup

    run_setup()


@setup_app.command("webui")
def webui() -> None:
    """Interfaz web de configuración (no disponible todavía)."""
    typer.echo("Próximamente — usá `inaki setup tui` por ahora.")
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _lanzar_tui() -> None:
    """Construye y ejecuta SetupApp."""
    from adapters.inbound.setup_tui.app import SetupApp

    app = SetupApp()
    app.run()
