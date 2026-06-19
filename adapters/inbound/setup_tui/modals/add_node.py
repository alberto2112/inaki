"""AddNodeModal — modal para añadir una sección o campo del schema.

Lista las opciones ``addable`` que el árbol computó para el nodo actual (claves
del schema NO presentes en el YAML). Al confirmar devuelve la ``AddableOption``
elegida; al cancelar, ``None``. Quien lo abre persiste el alta y repinta.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from adapters.inbound.setup_tui.domain.schema_node import AddableOption
from adapters.inbound.setup_tui.modals._dialog import dialog_css


class AddNodeModal(ModalScreen[AddableOption | None]):
    """Modal con ``ListView`` de las opciones añadibles en un nodo.

    Cada opción muestra su clave y, si el schema la expone, una descripción corta.
    Las secciones se marcan con ``/`` para distinguirlas de los campos simples.
    """

    DEFAULT_CSS = (
        dialog_css("AddNodeModal")
        + """
    AddNodeModal #dialog ListView {
        margin-top: 1;
        background: #0d0d0d;
        height: auto;
        max-height: 14;
        border: tall $primary;
    }
    AddNodeModal #dialog ListItem {
        padding: 0 1;
    }
    AddNodeModal #dialog ListItem.--highlight {
        background: $boost;
    }
    AddNodeModal .opt-desc {
        color: $text-muted;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("enter", "commit", show=False),
    ]

    def __init__(self, options: list[AddableOption], titulo: str = "añadir") -> None:
        super().__init__()
        self._options = options
        self._titulo = titulo
        self._by_key = {opt.key: opt for opt in options}

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._titulo, classes="titulo")
            with ListView(id="opciones"):
                for opt in self._options:
                    marca = "/" if opt.is_section else ""
                    texto = f"[bold]{opt.label}{marca}[/bold]"
                    if opt.description:
                        texto += f"\n[dim]{_resumen(opt.description)}[/dim]"
                    yield ListItem(Label(texto, markup=True), name=opt.key)
            yield Label(
                "[bold]↑↓[/bold] [dim]navegar[/dim]   "
                "[bold]enter[/bold] [dim]añadir[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def on_mount(self) -> None:
        lv = self.query_one("#opciones", ListView)
        lv.index = 0
        lv.focus()

    def action_commit(self) -> None:
        lv = self.query_one("#opciones", ListView)
        item = lv.highlighted_child
        if item is not None and item.name is not None:
            self.dismiss(self._by_key.get(item.name))
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _resumen(texto: str, limite: int = 60) -> str:
    """Primera línea del docstring, truncada para caber en el modal."""
    primera = texto.strip().splitlines()[0] if texto.strip() else ""
    return primera if len(primera) <= limite else primera[: limite - 1] + "…"
