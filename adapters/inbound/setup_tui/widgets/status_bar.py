"""StatusBar — barra inferior con bindings estáticos."""

from __future__ import annotations

from textual.widgets import Static


class StatusBar(Static):
    """Barra inferior estática con los atajos de teclado del contexto actual.

    Los modales tienen su propio footer interno — esta barra aplica a la
    pantalla principal únicamente.
    """

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    """

    _DEFAULT = (
        "[bold]↑↓[/bold] [dim]navegar[/dim]   "
        "[bold]enter[/bold] [dim]editar[/dim]   "
        "[bold]q[/bold] [dim]salir[/dim]   "
        "[bold]?[/bold] [dim]ayuda[/dim]"
    )

    def __init__(self, text: str | None = None) -> None:
        """``text`` permite a cada pantalla declarar sus propios atajos; sin él
        se usa el conjunto genérico."""
        super().__init__()
        self._text = text or self._DEFAULT

    def render(self) -> str:  # type: ignore[override]
        return self._text
