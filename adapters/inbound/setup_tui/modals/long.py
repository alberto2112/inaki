"""EditLongModal — modal con TextArea para campos multi-línea."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, TextArea

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.modals._dialog import dialog_css, initial_value_for_input


class EditLongModal(ModalScreen[str | None]):
    """Modal con ``TextArea`` para editar campos de texto largo.

    Ctrl+S guarda, Escape cancela. Retorna el texto completo del área o
    ``None`` si se cancela.
    """

    DEFAULT_CSS = (
        dialog_css("EditLongModal")
        + """
    EditLongModal #dialog TextArea {
        margin-top: 1;
        background: #0d0d0d;
        border: tall $primary;
        height: 12;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("ctrl+s", "save", show=False),
    ]

    def __init__(self, field: Field) -> None:
        super().__init__()
        self._field = field

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"editar  {self._field.label}", classes="titulo")
            yield TextArea(text=initial_value_for_input(self._field), id="editor")
            yield Label(
                "[bold]ctrl+s[/bold] [dim]guardar[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def on_mount(self) -> None:
        self.query_one("#editor", TextArea).focus()

    def action_save(self) -> None:
        self.dismiss(self.query_one("#editor", TextArea).text)

    def action_cancel(self) -> None:
        self.dismiss(None)
