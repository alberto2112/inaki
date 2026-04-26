"""EditSecretModal — modal con Input password para api_keys y tokens."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.modals._dialog import dialog_css, initial_value_for_input


class EditSecretModal(ModalScreen[str | None]):
    """Modal con ``Input password=True`` para editar campos secretos.

    Enter guarda, Escape cancela. Retorna el nuevo valor o ``None``.
    """

    DEFAULT_CSS = (
        dialog_css("EditSecretModal")
        + """
    EditSecretModal #dialog Input {
        margin-top: 1;
        background: #0d0d0d;
        border: tall $primary;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, field: Field) -> None:
        super().__init__()
        self._field = field

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"editar  {self._field.label} [dim](secret)[/dim]",
                classes="titulo",
            )
            inp = Input(value=initial_value_for_input(self._field), password=True, id="editor")
            inp.select_on_focus = False
            yield inp
            yield Label(
                "[bold]enter[/bold] [dim]guardar[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def on_mount(self) -> None:
        self.query_one("#editor", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)
