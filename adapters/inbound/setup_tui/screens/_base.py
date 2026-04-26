"""BasePage — clase base para todas las páginas de la TUI de setup."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import Screen

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.widgets.config_row import ConfigRow
from adapters.inbound.setup_tui.widgets.status_bar import StatusBar
from adapters.inbound.setup_tui.widgets.top_bar import TopBar


class BasePage(Screen):
    """Clase base para todas las páginas de edición de la TUI de setup.

    Layout estándar:
      - ``TopBar`` arriba (breadcrumb + versión).
      - ``ScrollableContainer`` en el medio (cuerpo con secciones + filas).
      - ``StatusBar`` abajo (bindings estáticos).

    Las subclases deben implementar:
      - ``breadcrumb()`` → str con el texto del breadcrumb.
      - ``compose_body()`` → ``ComposeResult`` con ``SectionHeader`` y ``ConfigRow``.

    La lógica de navegación (↑↓/j/k), edición (Enter) y callback post-edición
    viven acá para no duplicarse en cada página.
    """

    CSS = """
    Screen {
        background: #0d0d0d;
    }
    ScrollableContainer {
        scrollbar-size: 0 0;
    }
    """

    BINDINGS = [
        # priority=True evita que ScrollableContainer se coma las flechas para
        # su scroll-default antes de que llegue el binding al cursor lógico.
        Binding("up", "cursor_up", show=False, priority=True),
        Binding("k", "cursor_up", show=False, priority=True),
        Binding("down", "cursor_down", show=False, priority=True),
        Binding("j", "cursor_down", show=False, priority=True),
        Binding("enter", "edit", show=False),
        Binding("escape", "pop", show=False),
    ]

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._cursor_index: int = 0
        self._rows: list[ConfigRow] = []
        self._fields: list[Field] = []

    # ------------------------------------------------------------------
    # Hooks para subclases
    # ------------------------------------------------------------------

    def breadcrumb(self) -> str:
        """Texto del breadcrumb que muestra TopBar. Sobreescribir en subclases."""
        return "inaki / config"

    def compose_body(self) -> ComposeResult:
        """Yields ``SectionHeader`` y ``ConfigRow`` específicos de la página."""
        return
        yield  # necesario para que mypy infiera el tipo correcto

    # ------------------------------------------------------------------
    # compose estándar
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield TopBar(self.breadcrumb())
        with ScrollableContainer():
            yield from self.compose_body()
        yield StatusBar()

    def on_mount(self) -> None:
        # Recolectar las filas montadas para manejar el cursor
        self._rows = list(self.query(ConfigRow))
        self._fields = [row._field for row in self._rows]
        self._refresh_selection()

    # ------------------------------------------------------------------
    # Cursor
    # ------------------------------------------------------------------

    def _refresh_selection(self) -> None:
        """Actualiza la clase ``-selected`` en todas las filas y trae la
        seleccionada al viewport (sin animación para feel TUI clásico)."""
        for i, row in enumerate(self._rows):
            row.selected = i == self._cursor_index
        if self._rows:
            self._rows[self._cursor_index].scroll_visible(animate=False)

    def _current_field(self) -> Field:
        return self._fields[self._cursor_index]

    def _current_row(self) -> ConfigRow:
        return self._rows[self._cursor_index]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_cursor_up(self) -> None:
        if self._cursor_index > 0:
            self._cursor_index -= 1
            self._refresh_selection()

    def action_cursor_down(self) -> None:
        if self._cursor_index < len(self._rows) - 1:
            self._cursor_index += 1
            self._refresh_selection()

    def action_edit(self) -> None:
        """Abre el modal correspondiente al kind del campo seleccionado."""
        if not self._fields:
            return

        from adapters.inbound.setup_tui.modals.enum import EditEnumModal
        from adapters.inbound.setup_tui.modals.long import EditLongModal
        from adapters.inbound.setup_tui.modals.scalar import EditScalarModal
        from adapters.inbound.setup_tui.modals.secret import EditSecretModal

        field = self._current_field()
        if field.kind == "scalar":
            self.app.push_screen(EditScalarModal(field), self._after_edit)
        elif field.kind == "enum":
            self.app.push_screen(EditEnumModal(field), self._after_edit)
        elif field.kind == "long":
            self.app.push_screen(EditLongModal(field), self._after_edit)
        elif field.kind == "secret":
            self.app.push_screen(EditSecretModal(field), self._after_edit)

    def action_pop(self) -> None:
        """Vuelve a la pantalla anterior si no es la raíz del stack."""
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def _after_edit(self, result: str | None) -> None:
        """Callback llamado cuando el modal de edición cierra.

        - ``result is None`` → el usuario canceló el modal. No se hace nada.
        - ``result == "<null>"`` (escape hatch UX) → se interpreta como ``None``
          explícito y se persiste como ``null`` en YAML. Útil para campos
          opcionales como ``llm.reasoning_effort``.
        - cualquier otra cosa → se persiste el string tal cual.
        """
        if result is None:
            return

        field = self._current_field()
        # Convención UX: "<null>" tipeado significa guardar como None explícito
        field.value = None if result.strip() == "<null>" else result
        self._current_row().refresh_value()
        self._on_field_saved(field)

    def _on_field_saved(self, field: Field) -> None:
        """Hook llamado tras actualizar el valor de un campo.

        Las subclases lo sobreescriben para persistir el cambio en el repo.
        La implementación base no hace nada (útil para páginas de solo lectura).
        """
