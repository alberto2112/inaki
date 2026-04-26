"""MainMenuPage — pantalla de inicio con las 4 categorías de configuración."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import Screen
from textual.widgets import Label, Static

from adapters.inbound.setup_tui.widgets.status_bar import StatusBar
from adapters.inbound.setup_tui.widgets.top_bar import TopBar


class _MenuRow(Static):
    """Fila del menú principal: indicador + nombre de categoría + flecha."""

    DEFAULT_CSS = """
    _MenuRow {
        height: 1;
        padding: 0 2;
        layout: horizontal;
        background: transparent;
    }
    _MenuRow.-selected {
        background: $boost;
    }
    _MenuRow > .indicator {
        width: 2;
        content-align: left middle;
    }
    _MenuRow.-selected > .indicator {
        color: $accent;
    }
    _MenuRow > .label {
        width: 1fr;
        color: $text;
    }
    _MenuRow > .arrow {
        width: 4;
        color: $text-muted;
    }
    """

    def __init__(self, label: str) -> None:
        super().__init__()
        self._label = label
        self._selected = False

    def compose(self) -> ComposeResult:
        yield Label("", classes="indicator")
        yield Label(self._label, classes="label")
        yield Label(" →", classes="arrow")

    def set_selected(self, value: bool) -> None:
        """Activa o desactiva el highlight de la fila."""
        self._selected = value
        self.set_class(value, "-selected")
        try:
            self.query_one(".indicator", Label).update("▎" if value else " ")
        except Exception:
            pass


_MENU_ITEMS = [
    ("GLOBAL CONFIG", "global"),
    ("AGENTS", "agents"),
    ("PROVIDERS", "providers"),
    ("SECRETS", "secrets"),
]


class MainMenuPage(Screen):
    """Pantalla de inicio: lista las 4 categorías de configuración.

    Navegación con ↑↓/j/k. Enter abre la página correspondiente.
    """

    CSS = """
    Screen {
        background: #0d0d0d;
    }
    ScrollableContainer {
        scrollbar-size: 0 0;
    }
    """

    BINDINGS = [
        Binding("up", "cursor_up", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("j", "cursor_down", show=False),
        Binding("enter", "select", show=False),
        Binding("q", "quit", show=False),
        Binding("question_mark", "help", show=False),
    ]

    def __init__(self, container=None, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._container = container
        self._cursor_index = 0
        self._rows: list[_MenuRow] = []

    def compose(self) -> ComposeResult:
        yield TopBar("inaki / config")
        with ScrollableContainer():
            for label, _ in _MENU_ITEMS:
                row = _MenuRow(label)
                self._rows.append(row)
                yield row
        yield StatusBar()

    def on_mount(self) -> None:
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        for i, row in enumerate(self._rows):
            row.set_selected(i == self._cursor_index)

    def action_cursor_up(self) -> None:
        if self._cursor_index > 0:
            self._cursor_index -= 1
            self._refresh_selection()

    def action_cursor_down(self) -> None:
        if self._cursor_index < len(self._rows) - 1:
            self._cursor_index += 1
            self._refresh_selection()

    def action_select(self) -> None:
        """Navega a la página correspondiente a la fila seleccionada."""
        _, destino = _MENU_ITEMS[self._cursor_index]

        if destino == "global":
            from adapters.inbound.setup_tui.screens.global_page import GlobalPage

            self.app.push_screen(GlobalPage(self._container))
        elif destino == "agents":
            from adapters.inbound.setup_tui.screens.agents_page import AgentsPage

            self.app.push_screen(AgentsPage(self._container))
        elif destino == "providers":
            from adapters.inbound.setup_tui.screens.providers_page import ProvidersPage

            self.app.push_screen(ProvidersPage(self._container))
        elif destino == "secrets":
            from adapters.inbound.setup_tui.screens.secrets_page import SecretsPage

            self.app.push_screen(SecretsPage(self._container))

    def action_help(self) -> None:
        self.app.notify(
            "↑↓ navegar   enter abrir   q salir",
            title="Ayuda",
            timeout=4,
        )
