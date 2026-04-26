"""EditEnumModal — modal de selección de opciones para campos enum."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.modals._dialog import dialog_css


class EditEnumModal(ModalScreen[str | None]):
    """Modal con ``ListView`` vertical para seleccionar un valor de un enum.

    La opción actual queda pre-seleccionada en la lista. Retorna el valor
    elegido al confirmar con Enter, o ``None`` al cancelar con Escape.
    """

    DEFAULT_CSS = (
        dialog_css("EditEnumModal")
        + """
    EditEnumModal #dialog ListView {
        margin-top: 1;
        background: #0d0d0d;
        height: auto;
        max-height: 12;
        border: tall $primary;
    }
    EditEnumModal #dialog ListItem {
        padding: 0 1;
    }
    EditEnumModal #dialog ListItem.--highlight {
        background: $boost;
        color: $warning;
        text-style: bold;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("enter", "commit", show=False),
    ]

    def __init__(self, field: Field) -> None:
        super().__init__()
        self._field = field
        self._choices = field.enum_choices or ()

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"editar  {self._field.label}", classes="titulo")
            with ListView(id="opciones"):
                for choice in self._choices:
                    yield ListItem(Label(choice), name=choice)
            yield Label(
                "[bold]↑↓[/bold] [dim]navegar[/dim]   "
                "[bold]enter[/bold] [dim]ok[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def on_mount(self) -> None:
        lv = self.query_one("#opciones", ListView)
        current_val = str(self._field.value or "")
        try:
            current_idx = list(self._choices).index(current_val)
        except ValueError:
            current_idx = 0
        lv.index = current_idx
        lv.focus()

    def action_commit(self) -> None:
        lv = self.query_one("#opciones", ListView)
        item = lv.highlighted_child
        if item is not None and item.name is not None:
            self.dismiss(item.name)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
