"""ConfigRow — fila label+value con indicador de selección."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Label, Static

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.widgets._masking import mask_secret

_TRUNCATE_LIMIT = 42


class ConfigRow(Static):
    """Fila de configuración con indicador ``▎``, label y valor.

    La edición NO vive acá — se delega a los modales. Esta fila es puro
    presentación: muestra el estado actual del campo y reacciona a la selección.

    Atributos:
        selected: Reactivo — ``True`` cuando el cursor apunta a esta fila.
    """

    DEFAULT_CSS = """
    ConfigRow {
        height: 1;
        padding: 0 2;
        layout: horizontal;
        background: transparent;
    }
    ConfigRow.-selected {
        background: $boost;
    }
    ConfigRow > .indicator {
        width: 2;
        content-align: left middle;
    }
    ConfigRow.-selected > .indicator {
        color: $accent;
    }
    ConfigRow > .label {
        width: 28;
        color: $text;
    }
    ConfigRow > .value {
        width: 1fr;
        color: $success;
    }
    ConfigRow > .value.-dim-default {
        color: $text-muted;
        text-style: dim;
    }
    """

    selected: reactive[bool] = reactive(False)

    def __init__(self, field: Field) -> None:
        super().__init__()
        self._field = field

    def compose(self) -> ComposeResult:
        yield Label("", classes="indicator")
        yield Label(self._field.label, classes="label")
        yield Label(self._displayed_value(), classes="value")

    def watch_selected(self, value: bool) -> None:
        """Activa/desactiva la clase ``-selected`` y actualiza el indicador."""
        self.set_class(value, "-selected")
        try:
            indicator = self.query_one(".indicator", Label)
            indicator.update("▎" if value else " ")
        except Exception:
            pass

    def refresh_value(self) -> None:
        """Re-renderiza el Label de value tras cambiar ``_field.value``."""
        try:
            lbl = self.query_one(".value", Label)
            lbl.update(self._displayed_value())
        except Exception:
            pass

    def _displayed_value(self) -> str:
        """Valor formateado para mostrar en la fila.

        Distingue cuatro estados visuales:
          - Campo triestado en estado ``"inherit"``: ``(heredado)`` en dim italic.
          - Campo triestado en estado ``"override_null"``: ``<null>`` en amarillo.
          - Valor None explícito (escape hatch ``<null>`` del usuario): ``<null>`` en amarillo.
          - Valor vacío con default declarado: el default en dim con sufijo ``(default)``.
          - Valor configurado: el valor tal cual (truncado si ``kind == "long"``).
        """
        # Estado triestado: se muestra antes de los checks generales.
        if self._field.is_tristate:
            estado = self._field.tristate_state
            if estado == "inherit":
                return "[dim italic](heredado)[/dim italic]"
            if estado == "override_null":
                return "[yellow]<null>[/yellow]"
            # override_value cae al render normal del valor más abajo

        # Estado: el usuario seteó None explícitamente.
        # Color amarillo + texto `<null>` (simétrico con la convención del input).
        # No usamos `dim italic` porque combinado con el color verde de la clase
        # `.value` sale invisible en algunas terminales.
        if self._field.value is None:
            return "[yellow]<null>[/yellow]"

        val = str(self._field.value)

        # Estado 2: vacío + hay default → preview del default
        if not val and self._field.default is not None:
            return f"[dim]{self._field.default} (default)[/dim]"

        # Para secrets configurados, el row siempre muestra una versión enmascarada.
        # El valor REAL vive en field.value; este masking es solo de presentación
        # — el modal de edición recibe el field con el valor real para no corromper.
        if self._field.kind == "secret" and val:
            return mask_secret(val)

        if self._field.kind == "long":
            return _truncate(val, _TRUNCATE_LIMIT)

        return val


def _truncate(text: str, limit: int) -> str:
    """Trunca ``text`` a ``limit`` caracteres con ellipsis."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
