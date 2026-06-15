"""EditBoolModal — modal de edición tipo toggle para campos booleanos.

En lugar de tipear "true"/"false" en un Input (como hacía el modal scalar),
este modal muestra el estado actual y lo alterna con una sola tecla. Retorna
el booleano nativo elegido al confirmar con Enter, o ``None`` si se cancela
con Escape. Persistir un ``bool`` nativo (no el string "true") es lo que hace
que el YAML quede ``enabled: true`` y no ``enabled: "true"``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label

from adapters.inbound.setup_tui.domain.field import Field, coerce_bool
from adapters.inbound.setup_tui.modals._dialog import dialog_css


class EditBoolModal(ModalScreen[bool | None]):
    """Modal con un toggle ``true``/``false`` operado por teclado.

    Cualquier tecla de "cambio" (espacio, tab, flechas, hjkl, w/s) alterna el
    estado — como el valor es binario no hay "subir/bajar", solo flip. Enter
    confirma y retorna el ``bool``; Escape cancela y retorna ``None``.
    """

    DEFAULT_CSS = (
        dialog_css("EditBoolModal")
        + """
    EditBoolModal #dialog .toggle {
        margin-top: 1;
        height: 1;
        content-align: center middle;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("enter", "commit", show=False),
        # Todas las teclas de "cambio" alternan el valor binario.
        # Se usa "flip" y no "toggle" porque Textual ya define un
        # ``action_toggle(attribute_name)`` built-in en DOMNode.
        Binding("space", "flip", show=False),
        Binding("tab", "flip", show=False),
        Binding("up", "flip", show=False),
        Binding("down", "flip", show=False),
        Binding("left", "flip", show=False),
        Binding("right", "flip", show=False),
        Binding("h", "flip", show=False),
        Binding("j", "flip", show=False),
        Binding("k", "flip", show=False),
        Binding("l", "flip", show=False),
        Binding("w", "flip", show=False),
        Binding("s", "flip", show=False),
    ]

    def __init__(self, field: Field) -> None:
        super().__init__()
        self._field = field
        # Estado inicial: el valor actual si está seteado, sino el default.
        # ``0``/``False`` se tratan como valores configurados (no caen al default).
        raw = field.value if field.value not in (None, "") else field.default
        self._estado: bool = coerce_bool(raw)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"editar  {self._field.label}", classes="titulo")
            yield Label(self._render_toggle(), id="toggle", classes="toggle")
            yield Label(
                "[bold]espacio/tab/↑↓[/bold] [dim]alternar[/dim]   "
                "[bold]enter[/bold] [dim]guardar[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def _render_toggle(self) -> str:
        """Renderiza el toggle resaltando la opción activa."""
        if self._estado:
            return "[reverse bold] TRUE [/reverse bold]    [dim]false[/dim]"
        return "[dim]true[/dim]    [reverse bold] FALSE [/reverse bold]"

    def _refresh(self) -> None:
        """Re-renderiza el label del toggle tras un cambio de estado."""
        try:
            self.query_one("#toggle", Label).update(self._render_toggle())
        except Exception:
            pass  # sin contexto Textual montado (tests directos de la acción)

    def action_flip(self) -> None:
        self._estado = not self._estado
        self._refresh()

    def action_commit(self) -> None:
        self.dismiss(self._estado)

    def action_cancel(self) -> None:
        self.dismiss(None)
