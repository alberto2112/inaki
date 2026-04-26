"""EditTristateModal — modal de edición para campos con tri-estado.

Permite elegir entre tres modos:
  - ``"inherit"``        → la clave se omite, se hereda del config global.
  - ``"override_value"`` → valor explícito propio del agente.
  - ``"override_null"``  → null explícito (el agente anula el valor con None).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView

from adapters.inbound.setup_tui.domain.field import Field, TristateEstado
from adapters.inbound.setup_tui.modals._dialog import dialog_css, initial_value_for_input


@dataclass
class TristateResult:
    """Resultado que retorna ``EditTristateModal`` al cerrarse.

    Atributos:
        mode: Modo elegido por el usuario.
        value: Valor de texto ingresado. Solo relevante cuando
            ``mode == "override_value"``.
    """

    mode: Literal["inherit", "override_value", "override_null"]
    value: str | None = None


# Orden fijo de las 3 opciones tal como se muestran en el ListView.
_MODOS: list[tuple[str, str]] = [
    ("inherit", "Heredar (omitir clave)"),
    ("override_value", "Valor propio"),
    ("override_null", "null explícito"),
]

_MODO_INDEX: dict[str, int] = {modo: i for i, (modo, _) in enumerate(_MODOS)}


class EditTristateModal(ModalScreen["TristateResult | None"]):
    """Modal de tri-estado: heredar / valor propio / null explícito.

    Retorna un ``TristateResult`` al confirmarse o ``None`` si el usuario cancela.
    """

    DEFAULT_CSS = (
        dialog_css("EditTristateModal")
        + """
    EditTristateModal #dialog ListView {
        margin-top: 1;
        background: #0d0d0d;
        height: auto;
        max-height: 8;
        border: tall $primary;
    }
    EditTristateModal #dialog ListItem {
        padding: 0 1;
    }
    EditTristateModal #dialog ListItem.--highlight {
        background: $boost;
        color: $warning;
        text-style: bold;
    }
    EditTristateModal #dialog Input {
        margin-top: 1;
        background: #0d0d0d;
        border: tall $primary;
    }
    EditTristateModal #dialog Input:disabled {
        color: $text-muted;
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

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"editar  {self._field.label}  [dim](triestado)[/dim]",
                classes="titulo",
            )
            with ListView(id="modos"):
                for modo, etiqueta in _MODOS:
                    yield ListItem(Label(etiqueta), name=modo)
            inp = Input(
                value=initial_value_for_input(self._field),
                id="valor",
                disabled=True,
            )
            inp.select_on_focus = False
            yield inp
            yield Label(
                "[bold]↑↓[/bold] [dim]modo[/dim]   "
                "[bold]enter[/bold] [dim]ok[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def on_mount(self) -> None:
        lv = self.query_one("#modos", ListView)
        estado_actual: TristateEstado | None = self._field.tristate_state
        lv.index = _MODO_INDEX.get(estado_actual or "inherit", 0)
        lv.focus()
        # Habilitar el input si el estado inicial es "override_value"
        self._sync_input_state(estado_actual or "inherit")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Habilita el Input solo cuando el modo 'Valor propio' está seleccionado."""
        item = event.item
        modo = item.name if item is not None else "inherit"
        self._sync_input_state(modo or "inherit")

    def _sync_input_state(self, modo: str) -> None:
        inp = self.query_one("#valor", Input)
        if modo == "override_value":
            inp.disabled = False
        else:
            inp.disabled = True

    def action_commit(self) -> None:
        lv = self.query_one("#modos", ListView)
        item = lv.highlighted_child
        modo = item.name if item is not None else "inherit"
        modo_typed: Literal["inherit", "override_value", "override_null"] = modo  # type: ignore[assignment]

        inp = self.query_one("#valor", Input)
        valor = inp.value if modo == "override_value" else None

        self.dismiss(TristateResult(mode=modo_typed, value=valor))

    def action_cancel(self) -> None:
        self.dismiss(None)
