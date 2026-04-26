"""
SetupApp — aplicación Textual principal para la TUI de setup (v2).

Punto de entrada offline. Carga el contenedor liviano (``di.py``) y abre
el ``MainMenuPage``. NO requiere daemon corriendo.

Bienvenida de primera vez: al abrir la TUI por primera vez se muestra un
modal con instrucciones básicas. El flag se persiste en
``~/.inaki/setup_welcome_seen`` para no volver a mostrarlo.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label

from adapters.inbound.setup_tui.di import SetupContainer, build_setup_container
from adapters.inbound.setup_tui.modals._dialog import dialog_css

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
# Modal de bienvenida (estilo nuevo — borde recto, fondo dark)
# ---------------------------------------------------------------------------


class WelcomeModal(ModalScreen[None]):
    """Modal de bienvenida que se muestra una sola vez al primer lanzamiento."""

    DEFAULT_CSS = (
        dialog_css("WelcomeModal")
        + """
    WelcomeModal #dialog {
        max-height: 30;
    }
    WelcomeModal .body {
        margin-top: 1;
        color: $text;
    }
    """
    )

    BINDINGS = [
        Binding("enter", "cerrar", show=False),
        Binding("escape", "cerrar", show=False),
        Binding("space", "cerrar", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("inaki setup — TUI", classes="titulo")
            yield Label(
                "Bienvenido a [bold]inaki setup[/bold].\n\n"
                "Navegá con [bold]↑↓[/bold] o [bold]j/k[/bold]. "
                "Presioná [bold]Enter[/bold] para editar un campo.\n"
                "Los cambios se guardan inmediatamente.\n\n"
                "[yellow]Nota:[/yellow] el wizard Fernet ahora vive en:\n"
                "  [bold]inaki setup secret-key[/bold]",
                classes="body",
            )
            yield Label(
                "[bold]enter[/bold] [dim]continuar[/dim]   "
                "[bold]esc[/bold] [dim]cerrar[/dim]",
                classes="footer",
            )

    def action_cerrar(self) -> None:
        _marcar_welcome_vista()
        self.dismiss()


# ---------------------------------------------------------------------------
# Aplicación principal
# ---------------------------------------------------------------------------


class SetupApp(App):
    """
    Aplicación Textual para editar la configuración de inaki (v2).

    Offline-only: no conecta al daemon, no instancia LLM ni embedding.
    Todo el I/O va a ``~/.inaki/config/*.yaml`` via ``SetupContainer``.
    """

    TITLE = "inaki setup"
    SUB_TITLE = "Configuración offline"
    CSS_PATH = None

    CSS = """
    Screen {
        background: #0d0d0d;
    }
    ScrollableContainer {
        scrollbar-size: 0 0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Salir", show=False),
        Binding("question_mark", "ayuda", show=False),
        Binding("s", "guardar_mock", show=False),
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
        # El MainMenuPage se monta en on_mount para poder hacer push_screen.
        # compose no puede usar push_screen directamente.
        return
        yield  # type: ignore[misc]  # hace que mypy infiera ComposeResult

    async def on_mount(self) -> None:
        """Monta el menú principal y muestra bienvenida si es primera vez."""
        from adapters.inbound.setup_tui.screens.main_menu import MainMenuPage

        await self.push_screen(MainMenuPage(self.container))

        if not _welcome_ya_vista():
            self.push_screen(WelcomeModal())

    def action_ayuda(self) -> None:
        self.notify(
            "↑↓/jk navegar   enter editar   s guardar   q salir   esc volver",
            title="Ayuda rápida",
            timeout=5,
        )

    def action_guardar_mock(self) -> None:
        """Binding global 's' — los guardados reales ocurren per-edit en GlobalPage."""
        self.notify("guardar pendientes (mock)", title="setup", timeout=2)
