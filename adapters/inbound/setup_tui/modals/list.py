"""EditListModal — editor de listas de valores simples (int/str/float).

Reemplaza el texto raw para campos ``list[...]``: en vez de tipear ``[123, 456]``
a mano (contrario a la filosofía del setup), se agregan/quitan items uno por uno,
tipados. Cada item se parsea según ``field.list_item_type``; uno inválido se
rechaza con aviso. El ``ListView`` es la fuente de verdad: al confirmar se leen
sus ítems y se parsean a la lista final.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.modals._dialog import dialog_css


class EditListModal(ModalScreen["list | None"]):
    """Editor de una lista de valores simples.

    - Escribir en el Input + ``Enter`` → agrega un item (validado por tipo).
    - ``Enter`` sobre un item del listado → lo quita.
    - ``Ctrl+S`` → guarda (retorna la lista parseada al tipo del item).
    - ``Esc`` → cancela (retorna ``None``).
    """

    DEFAULT_CSS = (
        dialog_css("EditListModal")
        + """
    EditListModal #dialog ListView {
        margin-top: 1;
        background: #0d0d0d;
        height: auto;
        max-height: 10;
        border: tall $primary;
    }
    EditListModal #dialog ListItem { padding: 0 1; }
    EditListModal #dialog ListItem.--highlight {
        background: $boost;
        color: $warning;
        text-style: bold;
    }
    EditListModal #dialog Input {
        margin-top: 1;
        background: #0d0d0d;
        border: tall $primary;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("ctrl+s", "commit", show=False),
    ]

    def __init__(self, field: Field) -> None:
        super().__init__()
        self._field = field
        self._item_type = field.list_item_type or "str"
        raw = field.value if isinstance(field.value, list) else []
        self._initial = [str(x) for x in raw]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"editar  {self._field.label}  [dim](lista de {self._item_type})[/dim]",
                classes="titulo",
            )
            with ListView(id="items"):
                for it in self._initial:
                    yield ListItem(Label(it), name=it)
            inp = Input(placeholder=f"nuevo {self._item_type} + Enter para agregar", id="nuevo")
            inp.select_on_focus = False
            yield inp
            yield Label(
                "[bold]enter[/bold] [dim]agregar (input) / quitar (lista)[/dim]   "
                "[bold]tab[/bold] [dim]foco[/dim]   "
                "[bold]ctrl+s[/bold] [dim]guardar[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def on_mount(self) -> None:
        self.query_one("#nuevo", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter en el Input → agrega un item (validado por tipo)."""
        val = event.value.strip()
        if not val:
            return
        if not self._valido(val):
            self.app.notify(
                f"'{val}' no es un {self._item_type} válido", severity="warning", timeout=2
            )
            return
        self.query_one("#items", ListView).append(ListItem(Label(val), name=val))
        self.query_one("#nuevo", Input).value = ""

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter sobre un item del listado → lo quita."""
        if event.item is not None:
            event.item.remove()

    def action_commit(self) -> None:
        lv = self.query_one("#items", ListView)
        items: list = [
            self._parse(item.name) for item in lv.query(ListItem) if item.name is not None
        ]
        self.dismiss(items)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _parse(self, v: str) -> object:
        if self._item_type == "int":
            return int(v)
        if self._item_type == "float":
            return float(v)
        return v

    def _valido(self, v: str) -> bool:
        try:
            self._parse(v)
            return True
        except ValueError:
            return False
