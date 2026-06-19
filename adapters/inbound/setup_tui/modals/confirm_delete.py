"""ConfirmDeleteModal — confirmación antes de eliminar una sección o campo.

Para una sección lista los campos afectados (todo lo que se pierde al podar la
rama) — así el borrado nunca es ambiguo, que era justo la preocupación de la
propuesta visual. Devuelve ``True`` si el usuario confirma, ``False`` si cancela.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label

from adapters.inbound.setup_tui.modals._dialog import dialog_css


class ConfirmDeleteModal(ModalScreen[bool]):
    """Modal de confirmación de borrado.

    Args:
        titulo: Path legible de lo que se borra (ej. ``channels.telegram.groups``).
        es_seccion: Si ``True``, se muestra la lista de campos afectados.
        campos_afectados: Nombres de los campos que se pierden (solo secciones).
    """

    DEFAULT_CSS = (
        dialog_css("ConfirmDeleteModal")
        + """
    ConfirmDeleteModal #dialog {
        border: solid $error;
    }
    ConfirmDeleteModal .titulo {
        color: $error;
    }
    ConfirmDeleteModal .aviso {
        margin-top: 1;
        color: $text;
    }
    ConfirmDeleteModal .afectados {
        margin-top: 1;
        color: $warning;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("n", "cancel", show=False),
        Binding("enter", "confirm", show=False),
        Binding("y", "confirm", show=False),
    ]

    def __init__(
        self,
        titulo: str,
        es_seccion: bool,
        campos_afectados: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._titulo = titulo
        self._es_seccion = es_seccion
        self._campos = campos_afectados or []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("¿Eliminar?", classes="titulo")
            tipo = "la sección" if self._es_seccion else "el campo"
            yield Label(
                f"Vas a borrar {tipo} [bold]{self._titulo}[/bold] del YAML.",
                classes="aviso",
                markup=True,
            )
            if self._es_seccion and self._campos:
                yield Label(
                    "Se eliminan también: " + " · ".join(self._campos),
                    classes="afectados",
                )
            yield Label(
                "[bold]enter/y[/bold] [dim]eliminar[/dim]   [bold]esc/n[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
