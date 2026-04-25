"""
SetupApp — aplicación Textual principal para la TUI de setup.

Punto de entrada de la TUI offline. Carga el contenedor liviano (``di.py``)
y muestra las pantallas de configuración. NO requiere daemon corriendo.

Bienvenida de primera vez: al abrir la TUI por primera vez se muestra un
modal con nota sobre el rename de ``inaki setup`` (el wizard Fernet anterior
ahora vive en ``inaki setup secret-key``). El flag se persiste en
``~/.inaki/setup_welcome_seen`` para no volver a mostrarlo.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Label

from adapters.inbound.setup_tui.di import SetupContainer, build_setup_container

# ---------------------------------------------------------------------------
# Flag de bienvenida
# ---------------------------------------------------------------------------

_WELCOME_FLAG: Path = Path.home() / ".inaki" / "setup_welcome_seen"


def _welcome_ya_vista() -> bool:
    """Retorna True si el modal de bienvenida ya fue mostrado alguna vez."""
    return _WELCOME_FLAG.exists()


def _marcar_welcome_vista() -> None:
    """Persiste el flag para no volver a mostrar el modal de bienvenida."""
    _WELCOME_FLAG.parent.mkdir(parents=True, exist_ok=True)
    _WELCOME_FLAG.touch(exist_ok=True)


# ---------------------------------------------------------------------------
# Modal de bienvenida
# ---------------------------------------------------------------------------

_TEXTO_BIENVENIDA = """\
¡Bienvenido a [bold]inaki setup[/bold]!

Esta TUI reemplaza el wizard anterior de configuración.

[yellow]Nota importante:[/yellow]
El wizard de Fernet (para generar INAKI_SECRET_KEY) ahora está en:

  [bold]inaki setup secret-key[/bold]

Desde acá podés editar toda la configuración de inaki
sin tocar los archivos YAML a mano.

Usá [bold]Ctrl+S[/bold] para guardar · [bold]F1[/bold] para ayuda · [bold]Q[/bold] para salir.
"""


class WelcomeModal(ModalScreen[None]):
    """Modal de bienvenida que se muestra una sola vez al primer lanzamiento."""

    CSS = """
    WelcomeModal {
        align: center middle;
    }
    #dialog {
        grid-size: 1;
        grid-gutter: 1 2;
        padding: 2 4;
        width: 70;
        height: auto;
        border: thick $background 80%;
        background: $surface;
    }
    #titulo {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    #mensaje {
        margin-bottom: 1;
    }
    #btn-ok {
        width: 100%;
        dock: bottom;
    }
    """

    BINDINGS = [
        Binding("enter", "cerrar", "OK", show=False),
        Binding("escape", "cerrar", "OK", show=False),
    ]

    def compose(self) -> ComposeResult:
        from textual.containers import Grid

        with Grid(id="dialog"):
            yield Label("inaki setup — TUI", id="titulo")
            yield Label(_TEXTO_BIENVENIDA, id="mensaje", markup=True)
            yield Button("Entendido", variant="primary", id="btn-ok")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.action_cerrar()

    def action_cerrar(self) -> None:
        _marcar_welcome_vista()
        self.dismiss()


# ---------------------------------------------------------------------------
# Aplicación principal
# ---------------------------------------------------------------------------


class SetupApp(App):
    """
    Aplicación Textual para editar la configuración de inaki.

    Offline-only: no conecta al daemon, no instancia LLM ni embedding.
    Todo el I/O va a ``~/.inaki/config/*.yaml`` via ``SetupContainer``.
    """

    TITLE = "inaki setup"
    SUB_TITLE = "Configuración offline"
    CSS_PATH = None  # estilos inline en cada pantalla

    BINDINGS = [
        Binding("ctrl+s", "guardar", "Guardar", show=True),
        Binding("f1", "ayuda", "Ayuda", show=True),
        Binding("1", "ir_global", "Global", show=True),
        Binding("2", "ir_providers", "Providers", show=True),
        Binding("3", "ir_agentes", "Agentes", show=True),
        Binding("4", "ir_secrets", "Secrets", show=True),
        Binding("q", "quit", "Salir", show=True),
    ]

    def __init__(
        self,
        container: SetupContainer | None = None,
        config_dir: Path | None = None,
        **kwargs,  # type: ignore[no-untyped-def]
    ) -> None:
        """
        Args:
            container: Contenedor pre-construido (útil en tests). Si es ``None``
                       se construye uno via ``build_setup_container``.
            config_dir: Override del directorio de config (para tests).
        """
        super().__init__(**kwargs)
        self.container: SetupContainer = container or build_setup_container(config_dir)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        # La pantalla inicial se monta en on_mount para poder hacer push_screen
        # (compose se llama antes de que el event loop arranque).

    async def on_mount(self) -> None:
        """Monta la pantalla inicial y muestra bienvenida si es primera vez."""
        from adapters.inbound.setup_tui.screens.global_screen import GlobalScreen

        await self.push_screen(GlobalScreen(self.container))

        if not _welcome_ya_vista():
            self.push_screen(WelcomeModal())

    # ------------------------------------------------------------------
    # Acciones de teclado
    # ------------------------------------------------------------------

    async def action_guardar(self) -> None:
        """Delega Ctrl+S a la pantalla activa si implementa _guardar()."""
        pantalla_activa = self.screen
        if hasattr(pantalla_activa, "_guardar"):
            await pantalla_activa._guardar()  # type: ignore[attr-defined]

    def action_ayuda(self) -> None:
        """Muestra un modal de ayuda básica de teclas."""
        self.notify(
            "Ctrl+S guardar · F1 ayuda · 1-4 navegar pantallas · Q salir",
            title="Ayuda rápida",
            timeout=5,
        )

    async def action_ir_global(self) -> None:
        from adapters.inbound.setup_tui.screens.global_screen import GlobalScreen

        await self._navegar_a(GlobalScreen)

    async def action_ir_providers(self) -> None:
        from adapters.inbound.setup_tui.screens.providers_screen import ProvidersScreen

        await self._navegar_a(ProvidersScreen)

    async def action_ir_agentes(self) -> None:
        from adapters.inbound.setup_tui.screens.agents_screen import AgentsScreen

        await self._navegar_a(AgentsScreen)

    async def action_ir_secrets(self) -> None:
        from adapters.inbound.setup_tui.screens.secrets_screen import SecretsScreen

        await self._navegar_a(SecretsScreen)

    async def _navegar_a(self, clase_pantalla: type) -> None:
        """Reemplaza la pantalla raíz con una nueva instancia de la pantalla dada."""
        # pop hasta llegar a la raíz (WelcomeModal puede estar arriba)
        while len(self.screen_stack) > 1:
            self.pop_screen()
        nueva = clase_pantalla(self.container)
        await self.push_screen(nueva)
