"""
MaskedInput — widget de entrada de texto con enmascaramiento para secrets.

Reglas de display (UX-decision#2):
  - Vacío (len == 0) → muestra ""
  - len < 12          → muestra "••••••••" (8 bullets fijos)
  - len >= 12         → muestra "XXXXX•••YYYY" (5 primeros + bullets + 4 últimos)

Toggle Reveal (F2): muestra el valor real temporalmente hasta el próximo F2.

El valor real se almacena en ``_valor_real`` y siempre es accesible via
la propiedad ``valor``. El display es read-only para el usuario — la edición
se hace a través del campo ``Input`` subyacente que se muestra al hacer Reveal.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, Static

_BULLETS = "••••••••"
_N_PRIMEROS = 5
_N_ULTIMOS = 4
_BULLET_MEDIO = "•"


def _mascara(valor: str) -> str:
    """
    Calcula el texto de display enmascarado para ``valor``.

    >>> _mascara("") == ""
    True
    >>> _mascara("abc") == "••••••••"
    True
    >>> _mascara("x" * 11) == "••••••••"
    True
    >>> len(_mascara("x" * 12)) > 8
    True
    """
    if not valor:
        return ""
    n = len(valor)
    if n < 12:
        return _BULLETS
    # len >= 12: primeros 5 + bullets de relleno + últimos 4
    primeros = valor[:_N_PRIMEROS]
    ultimos = valor[-_N_ULTIMOS:]
    bullets_medio = _BULLET_MEDIO * max(1, n - _N_PRIMEROS - _N_ULTIMOS)
    return f"{primeros}{bullets_medio}{ultimos}"


class MaskedInput(Static):
    """
    Widget para ingresar/mostrar un secret con enmascaramiento configurable.

    Emite ``MaskedInput.Changed`` cuando el valor cambia.
    Emite ``MaskedInput.RevealToggled`` cuando se activa/desactiva el reveal.
    """

    BINDINGS = [
        Binding("f2", "toggle_reveal", "Reveal", show=True),
    ]

    # Reactive que controla si estamos en modo reveal
    revelado: reactive[bool] = reactive(False)

    class Changed(Message):
        """El valor del campo cambió."""

        def __init__(self, widget: "MaskedInput", valor: str) -> None:
            super().__init__()
            self.widget = widget
            self.valor = valor

    class RevealToggled(Message):
        """El modo reveal fue activado o desactivado."""

        def __init__(self, widget: "MaskedInput", revelado: bool) -> None:
            super().__init__()
            self.widget = widget
            self.revelado = revelado

    def __init__(
        self,
        valor: str = "",
        placeholder: str = "",
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._valor_real: str = valor
        self._placeholder = placeholder

    # ------------------------------------------------------------------
    # Propiedad pública
    # ------------------------------------------------------------------

    @property
    def valor(self) -> str:
        """El valor real (sin enmascarar)."""
        return self._valor_real

    @valor.setter
    def valor(self, nuevo: str) -> None:
        self._valor_real = nuevo
        self._actualizar_display()
        self.post_message(self.Changed(self, nuevo))

    # ------------------------------------------------------------------
    # Compose / render
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # Input oculto por defecto — se muestra en modo reveal
        yield Input(
            value=self._valor_real,
            placeholder=self._placeholder,
            password=False,
            id="input-real",
            classes="hidden",
        )
        yield Static(
            self._display_text(),
            id="display-masked",
        )

    def on_mount(self) -> None:
        self._actualizar_display()

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------

    def action_toggle_reveal(self) -> None:
        """Alterna entre modo enmascarado y modo reveal."""
        self.revelado = not self.revelado
        self.post_message(self.RevealToggled(self, self.revelado))

    def watch_revelado(self, nuevo_valor: bool) -> None:
        """Reacciona al cambio de modo reveal."""
        self._actualizar_display()

    # ------------------------------------------------------------------
    # Eventos del Input interno
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        """Sincroniza el valor real cuando cambia el Input interno."""
        event.stop()
        self._valor_real = event.value
        self._actualizar_display()
        self.post_message(self.Changed(self, self._valor_real))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _display_text(self) -> str:
        if self.revelado:
            return self._valor_real or self._placeholder
        return _mascara(self._valor_real) if self._valor_real else self._placeholder

    def _actualizar_display(self) -> None:
        """Actualiza los widgets hijo según el estado actual."""
        try:
            display = self.query_one("#display-masked", Static)
            input_real = self.query_one("#input-real", Input)
        except Exception:
            return  # Aún no montado

        if self.revelado:
            display.add_class("hidden")
            input_real.remove_class("hidden")
            input_real.value = self._valor_real
        else:
            input_real.add_class("hidden")
            display.remove_class("hidden")
            display.update(self._display_text())
