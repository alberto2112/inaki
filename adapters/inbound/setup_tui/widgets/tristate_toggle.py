"""
TristateToggle — widget de tres estados para campos con herencia de config.

Los tres estados mapean directamente a ``TristadoValor`` del use case:
  - INHERIT       → omite la clave del YAML (hereda del nivel superior)
  - OVERRIDE_VALUE → escribe el valor explícito en la capa del agente
  - OVERRIDE_NULL  → escribe ``null`` explícito (pisa con None)

Es crucial que estos tres estados se emitan de forma distinta al use case:
  - INHERIT → no aparece en el dict de cambios (o aparece como CampoTriestado(INHERIT))
  - OVERRIDE_NULL → aparece como ``null`` explícito en el YAML
  - OVERRIDE_VALUE → aparece con el valor

El widget emite ``TristateToggle.Changed`` cuando el estado cambia.
"""

from __future__ import annotations

from enum import Enum

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Label, Static


class TristateValorUI(str, Enum):
    """Estados del TristateToggle en la UI."""

    INHERIT = "inherit"
    """Hereda del nivel superior (no escribe la clave)."""

    OVERRIDE_VALUE = "value"
    """Escribe el valor explícito en la capa del agente."""

    OVERRIDE_NULL = "null"
    """Escribe ``null`` explícito en la capa del agente."""


_LABELS: dict[TristateValorUI, str] = {
    TristateValorUI.INHERIT: "Heredar",
    TristateValorUI.OVERRIDE_VALUE: "Valor propio",
    TristateValorUI.OVERRIDE_NULL: "null explícito",
}

_ORDEN: list[TristateValorUI] = [
    TristateValorUI.INHERIT,
    TristateValorUI.OVERRIDE_VALUE,
    TristateValorUI.OVERRIDE_NULL,
]


class TristateToggle(Static):
    """
    Widget de tres estados para campos con herencia de configuración de agente.

    Emite ``TristateToggle.Changed`` cuando el estado cambia.
    Cicla entre los tres estados con la tecla Espacio o haciendo click en
    el botón del estado activo.
    """

    BINDINGS = [
        Binding("space", "ciclar", "Ciclar estado", show=False),
    ]

    estado: reactive[TristateValorUI] = reactive(TristateValorUI.INHERIT)

    class Changed(Message):
        """El estado del TristateToggle cambió."""

        def __init__(self, widget: "TristateToggle", estado: TristateValorUI) -> None:
            super().__init__()
            self.widget = widget
            self.estado = estado

    def __init__(
        self,
        estado_inicial: TristateValorUI = TristateValorUI.INHERIT,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._estado_inicial = estado_inicial

    def compose(self) -> ComposeResult:
        for estado in _ORDEN:
            label = _LABELS[estado]
            variante = "primary" if estado == self.estado else "default"
            yield Button(label, variant=variante, id=f"btn-{estado.value}", classes="tristate-btn")

    def on_mount(self) -> None:
        self.estado = self._estado_inicial
        self._actualizar_botones()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Selecciona el estado correspondiente al botón presionado."""
        event.stop()
        btn_id = event.button.id or ""
        for estado in TristateValorUI:
            if btn_id == f"btn-{estado.value}":
                self.estado = estado
                break

    def action_ciclar(self) -> None:
        """Cicla al siguiente estado en el orden predefinido."""
        idx_actual = _ORDEN.index(self.estado)
        self.estado = _ORDEN[(idx_actual + 1) % len(_ORDEN)]

    def watch_estado(self, nuevo: TristateValorUI) -> None:
        """Reacciona al cambio de estado: actualiza botones y emite mensaje."""
        self._actualizar_botones()
        self.post_message(self.Changed(self, nuevo))

    def _actualizar_botones(self) -> None:
        """Resalta el botón del estado activo."""
        for estado in TristateValorUI:
            try:
                btn = self.query_one(f"#btn-{estado.value}", Button)
                if estado == self.estado:
                    btn.variant = "primary"
                else:
                    btn.variant = "default"
            except Exception:
                pass  # Aún no montado
