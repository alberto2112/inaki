"""
Sub-app Typer para el comando ``inaki setup``.

Comandos disponibles:

  ``inaki setup``           → abre la TUI (alias de ``tui``)
  ``inaki setup tui``       → abre la TUI Textual de configuración offline
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


@setup_app.command("webui")
def webui() -> None:
    """Interfaz web de configuración (no disponible todavía)."""
    typer.echo("Próximamente — usá `inaki setup tui` por ahora.")
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _lanzar_tui() -> None:
    """Construye y ejecuta SetupApp.

    Composition root: acá (en ``inaki/``, fuera de ``adapters/``) es legítimo
    importar las clases de schema de ``infrastructure.config`` e inyectarlas en
    el ``SetupContainer`` — los screens del setup_tui las consumen sin conocer
    a infrastructure.
    """
    from adapters.inbound.setup_tui.app import SetupApp
    from adapters.inbound.setup_tui.di import build_setup_container
    from infrastructure.config import AgentConfig, GlobalConfig, TelegramChannelConfig
    from infrastructure.factories.embedding_factory import EmbeddingProviderFactory
    from infrastructure.factories.llm_factory import LLMProviderFactory
    from infrastructure.factories.transcription_factory import TranscriptionProviderFactory

    # Adaptadores de proveedor disponibles (autodescubiertos por las factories).
    # Alimentan el desplegable de TIPO en la página de providers. Los choices del
    # árbol (`*.provider` → providers declarados; `*.agent_id` → sub-agentes) los
    # resuelve `setup_tui.choices.resolve_choices` con el repo, no esta lista.
    provider_choices = tuple(
        sorted(
            set(LLMProviderFactory.available())
            | set(EmbeddingProviderFactory.available())
            | set(TranscriptionProviderFactory.available())
        )
    )

    container = build_setup_container(
        config_dir=None,
        global_schema=GlobalConfig,
        agent_schema=AgentConfig,
        # Registry de canales para introspeccionar el dict ``channels`` del agente.
        # Al sumar un canal nuevo (slack, etc.) agregar su modelo acá.
        channel_schemas={"telegram": TelegramChannelConfig},
        provider_adapters=provider_choices,
    )
    app = SetupApp(container)
    app.run()
