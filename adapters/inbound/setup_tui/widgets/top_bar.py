"""TopBar — barra superior con breadcrumb y versión."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label, Static

try:
    from inaki import __version__ as _VERSION
except Exception:
    _VERSION = "v?"


class TopBar(Static):
    """Barra superior: breadcrumb a la izquierda, versión a la derecha."""

    DEFAULT_CSS = """
    TopBar {
        height: 1;
        padding: 0 2;
        layout: horizontal;
        color: $text-muted;
    }
    TopBar > .path {
        width: 1fr;
        color: $text-muted;
    }
    TopBar > .version {
        width: auto;
        color: $text-muted;
    }
    """

    def __init__(self, breadcrumb: str = "inaki / config") -> None:
        super().__init__()
        self._breadcrumb = breadcrumb

    def compose(self) -> ComposeResult:
        yield Label(self._breadcrumb, classes="path")
        yield Label(_VERSION, classes="version")

    def set_breadcrumb(self, breadcrumb: str) -> None:
        """Actualiza el texto del breadcrumb en tiempo de ejecución."""
        self._breadcrumb = breadcrumb
        try:
            self.query_one(".path", Label).update(breadcrumb)
        except Exception:
            pass
