"""DetailRow — fila del panel de detalle (TUI v3).

Renderiza, con columnas alineadas y un indicador de selección, dos clases de
ítem del panel:
  - **campo presente**: ``clave`` (izq) + ``valor`` (der, coloreado por tipo) →
    editable con Enter.
  - **opción addable**: ``+ clave`` (verde) + una pista (``sección`` / tipo) →
    al pulsar Enter se añade.

Ambos comparten cursor: el panel es una única lista navegable y accionable.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Label, Static

from adapters.inbound.setup_tui.domain.field import Field, coerce_bool
from adapters.inbound.setup_tui.widgets._masking import mask_secret

_TRUNCATE = 46


class DetailRow(Static):
    """Fila del panel: indicador ▎ + clave + valor/pista. Selección reactiva."""

    DEFAULT_CSS = """
    DetailRow {
        height: 1;
        layout: horizontal;
        background: transparent;
        padding: 0 1;
    }
    DetailRow.-selected {
        background: $boost;
    }
    DetailRow > .ind {
        width: 2;
        color: $accent;
    }
    DetailRow > .key {
        width: 26;
        color: $text;
    }
    DetailRow.-add > .key {
        color: $success;
    }
    DetailRow > .val {
        width: 1fr;
        color: $success;
    }
    DetailRow > .val.-muted { color: $text-muted; }
    DetailRow > .val.-enum { color: $warning; }
    DetailRow > .val.-bool { color: #41d4c8; }
    DetailRow > .val.-num { color: #f59b42; }
    DetailRow > .val.-secret { color: $text-muted; }
    """

    selected: reactive[bool] = reactive(False)

    def __init__(
        self, *, key: str, value_markup: str, is_add: bool, muted: bool, value_class: str = ""
    ) -> None:
        super().__init__()
        self._key = key
        self._value_markup = value_markup
        self._is_add = is_add
        self._muted = muted
        self._value_class = value_class

    def compose(self) -> ComposeResult:
        if self._is_add:
            self.add_class("-add")
        yield Label(" ", classes="ind")
        yield Label(self._key, classes="key")
        cls = "val -muted" if self._muted else f"val {self._value_class}".strip()
        yield Label(self._value_markup, classes=cls, markup=True)

    def watch_selected(self, value: bool) -> None:
        self.set_class(value, "-selected")
        try:
            self.query_one(".ind", Label).update("▎" if value else " ")
        except Exception:
            pass

    def refresh_value(self, value_markup: str, muted: bool, value_class: str = "") -> None:
        """Re-renderiza la columna de valor tras una edición."""
        self._value_markup = value_markup
        self._muted = muted
        self._value_class = value_class
        try:
            val = self.query_one(".val", Label)
            val.update(value_markup)
            val.set_classes(("val -muted" if muted else f"val {value_class}").strip())
        except Exception:
            pass


def field_value_class(field: Field) -> str:
    """Clase CSS de color para el valor según su tipo (-enum/-bool/-num/-secret)."""
    if field.is_tristate and field.tristate_state in ("inherit", "override_null"):
        return "-muted"
    if field.kind == "enum":
        return "-enum"
    if field.kind == "bool":
        return "-bool"
    if field.kind == "secret":
        return "-secret"
    if isinstance(field.value, bool):
        return "-bool"
    if isinstance(field.value, (int, float)):
        return "-num"
    return ""


def field_value_markup(field: Field) -> tuple[str, bool]:
    """Devuelve ``(markup, muted)`` para mostrar el valor de un campo.

    ``muted`` indica que el texto va en color atenuado (heredado / default /
    sin configurar) — separa lo configurado de lo derivado.
    """
    if field.is_tristate:
        if field.tristate_state == "inherit":
            return "[italic](heredado)[/italic]", True
        if field.tristate_state == "override_null":
            return "[yellow]<null>[/yellow]", False

    if field.kind == "bool" and field.value not in (None, ""):
        return ("true" if coerce_bool(field.value) else "false"), False

    if field.value is None:
        return "[yellow]<null>[/yellow]", False

    val = str(field.value)
    if not val:
        return (f"{field.default} (default)" if field.default is not None else "—"), True
    if field.kind == "secret":
        return mask_secret(val), False
    if len(val) > _TRUNCATE:
        val = val[: _TRUNCATE - 1] + "…"
    return val, False
