"""BasePage — clase base para todas las páginas de la TUI de setup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import Screen

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.widgets.config_row import ConfigRow
from adapters.inbound.setup_tui.widgets.status_bar import StatusBar
from adapters.inbound.setup_tui.widgets.top_bar import TopBar

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.modals.tristate import TristateResult


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

        field = self._current_field()

        # Los campos triestados tienen su propio modal independiente del kind.
        if field.is_tristate:
            from adapters.inbound.setup_tui.modals.tristate import EditTristateModal

            self.app.push_screen(EditTristateModal(field), self._after_tristate_edit)
            return

        from adapters.inbound.setup_tui.modals.enum import EditEnumModal
        from adapters.inbound.setup_tui.modals.long import EditLongModal
        from adapters.inbound.setup_tui.modals.scalar import EditScalarModal
        from adapters.inbound.setup_tui.modals.secret import EditSecretModal

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

    # ------------------------------------------------------------------
    # Triestado
    # ------------------------------------------------------------------

    def _after_tristate_edit(self, result: "TristateResult | None") -> None:
        """Callback llamado cuando el modal triestado cierra.

        - ``result is None`` → el usuario canceló. No se hace nada.
        - De lo contrario se actualiza el campo en memoria y se llama al hook.
        """
        if result is None:
            return

        field = self._current_field()
        field.tristate_state = result.mode  # type: ignore[assignment]

        if result.mode == "override_value":
            field.value = result.value or ""
        elif result.mode == "override_null":
            field.value = None
        else:  # inherit
            field.value = ""

        self._current_row().refresh_value()
        self._on_tristate_field_saved(field, result)

    def _on_tristate_field_saved(self, field: Field, result: "TristateResult") -> None:
        """Hook para que las subclases persistan el cambio de un campo triestado.

        La implementación base no hace nada.
        """

    # ------------------------------------------------------------------
    # Validación cross-ref post-save
    # ------------------------------------------------------------------

    def _warn_on_invalid_refs(self) -> None:
        """Post-save: valida referencias cruzadas globales y notifica si hay fallas.

        El save ya pasó cuando llamamos a esto — nuestra responsabilidad es
        avisar al usuario que rompió una referencia (ej. ``app.default_agent``
        apuntando a un agente que no existe). NO desarmar el cambio.

        Si la validación lanza ``ReferenciaInvalidaError``, mostramos el
        warning con el mensaje del error. Si lanza otra cosa (típicamente un
        ``ValidationError`` de Pydantic porque el YAML quedó estructuralmente
        roto), mostramos un warning genérico para que el usuario sepa que
        algo está mal sin perder el cambio.
        """
        container = getattr(self, "_container", None)
        if container is None:
            return

        from core.domain.errors import ReferenciaInvalidaError

        try:
            from infrastructure.config import GlobalConfig

            from adapters.inbound.setup_tui.validators.cross_refs import (
                validate_global_config,
            )

            efectiva = container.get_effective_config.execute()
            cfg = GlobalConfig(**efectiva.datos)
            available_agents = container.list_agents.execute()
            available_providers = [
                p.key for p in container.list_providers.execute()
            ]
            validate_global_config(cfg, available_agents, available_providers)
        except ReferenciaInvalidaError as exc:
            self.app.notify(
                f"⚠ {exc}",
                title="referencia inválida",
                severity="warning",
                timeout=6,
            )
        except Exception as exc:
            self.app.notify(
                f"⚠ config inválida tras guardar: {type(exc).__name__}",
                title="advertencia",
                severity="warning",
                timeout=6,
            )
