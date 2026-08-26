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
    from infrastructure.config import CHANNEL_SCHEMAS, AgentConfig, GlobalConfig
    from infrastructure.home import get_inaki_home
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

    home = get_inaki_home()
    # Bootstrap + migraciones ANTES de construir el container: la TUI solo lee
    # las capas actuales, así que sobre una instalación sin migrar mostraría
    # credenciales "sin configurar" (viven en un secrets aún no plegado), un
    # valor nuevo tipeado ahí sería pisado por el fold del siguiente arranque,
    # y un secrets huérfano de un agente borrado resucitaría como agente roto.
    from infrastructure.config import ensure_user_config

    ensure_user_config(home / "config", home / "agents")
    container = build_setup_container(
        config_dir=home / "config",
        agents_dir=home / "agents",
        global_schema=GlobalConfig,
        agent_schema=AgentConfig,
        # Registry de canales para introspeccionar el dict ``channels`` del agente.
        # Es el MISMO que valida el loader (``CHANNEL_SCHEMAS``): sumar un canal
        # es una sola entrada allá, no una lista que mantener sincronizada acá.
        channel_schemas=dict(CHANNEL_SCHEMAS),
        provider_adapters=provider_choices,
    )
    app = SetupApp(container)
    app.run()
