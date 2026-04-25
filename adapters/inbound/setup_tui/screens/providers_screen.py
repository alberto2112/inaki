"""
ProvidersScreen — CRUD de providers del registry.

Lista providers sin exponer api_key. Para agregar o editar, la api_key
va siempre a ``global.secrets.yaml``. Eliminar pide confirmación si el
provider tiene api_key definida.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from adapters.inbound.setup_tui.widgets.masked_input import MaskedInput

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer
    from core.use_cases.config.list_providers import ProviderInfo


# ---------------------------------------------------------------------------
# Modal de edición de provider
# ---------------------------------------------------------------------------


class _EditarProviderModal(ModalScreen[dict | None]):
    """Modal para crear/editar un provider."""

    CSS = """
    _EditarProviderModal {
        align: center middle;
    }
    #dialog {
        width: 70;
        height: auto;
        padding: 2 4;
        border: thick $background 80%;
        background: $surface;
    }
    """

    def __init__(
        self,
        key: str = "",
        type: str = "",
        base_url: str = "",
        tiene_api_key: bool = False,
        es_nuevo: bool = True,
    ) -> None:
        super().__init__()
        self._key = key
        self._type = type
        self._base_url = base_url
        self._tiene_api_key = tiene_api_key
        self._es_nuevo = es_nuevo

    def compose(self) -> ComposeResult:
        titulo = "Nuevo provider" if self._es_nuevo else f"Editar: {self._key}"
        with Vertical(id="dialog"):
            yield Label(f"[bold]{titulo}[/bold]", markup=True)
            yield Label("Key (nombre):")
            yield Input(value=self._key, id="input-key", disabled=not self._es_nuevo)
            yield Label("Type (opcional):")
            yield Input(value=self._type, id="input-type")
            yield Label("Base URL (opcional):")
            yield Input(value=self._base_url, id="input-base-url")
            yield Label("API Key (vacío = no modificar):")
            yield MaskedInput(valor="", id="input-api-key")
            with Horizontal():
                yield Button("Guardar", variant="primary", id="btn-guardar")
                yield Button("Cancelar", variant="default", id="btn-cancelar")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-guardar":
            try:
                key_input = self.query_one("#input-key", Input)
                type_input = self.query_one("#input-type", Input)
                base_url_input = self.query_one("#input-base-url", Input)
                api_key_widget = self.query_one("#input-api-key", MaskedInput)

                key = key_input.value.strip()
                if not key:
                    self.notify("La key no puede estar vacía.", severity="error")
                    return

                self.dismiss({
                    "key": key,
                    "type": type_input.value.strip() or None,
                    "base_url": base_url_input.value.strip() or None,
                    "api_key": api_key_widget.valor.strip() or None,
                })
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")
        elif event.button.id == "btn-cancelar":
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal de confirmación de eliminación
# ---------------------------------------------------------------------------


class _ConfirmarEliminarModal(ModalScreen[str | None]):
    """Modal para confirmar eliminación de provider y gestión de api_key."""

    def __init__(self, key: str, tiene_api_key: bool) -> None:
        super().__init__()
        self._key = key
        self._tiene_api_key = tiene_api_key

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"¿Eliminar provider [bold]{self._key}[/bold]?", markup=True)
            if self._tiene_api_key:
                yield Label("[yellow]Este provider tiene api_key guardada en secrets.[/yellow]", markup=True)
                yield Button("Eliminar + borrar api_key", variant="error", id="btn-eliminar-todo")
                yield Button("Eliminar (mantener api_key)", variant="warning", id="btn-eliminar-sin-key")
            else:
                yield Button("Eliminar", variant="error", id="btn-eliminar")
            yield Button("Cancelar", variant="default", id="btn-cancelar")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-eliminar-todo":
            self.dismiss("con_key")
        elif btn_id in ("btn-eliminar-sin-key", "btn-eliminar"):
            self.dismiss("sin_key")
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Pantalla principal
# ---------------------------------------------------------------------------


class ProvidersScreen(Screen):
    """CRUD de providers del registry global."""

    BINDINGS = [
        Binding("n", "nuevo_provider", "Nuevo", show=True),
        Binding("delete", "eliminar_provider", "Eliminar", show=True),
        Binding("escape", "volver", "Volver", show=True),
    ]

    def __init__(self, container: "SetupContainer") -> None:
        super().__init__()
        self._container = container
        self._providers: list[ProviderInfo] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ScrollableContainer():
            yield Label("[bold]Providers[/bold]", markup=True)
            yield DataTable(id="tabla-providers")
            with Static():
                yield Button("Nuevo (N)", variant="primary", id="btn-nuevo")
                yield Button("Editar", variant="default", id="btn-editar")
                yield Button("Eliminar (Del)", variant="error", id="btn-eliminar")
                yield Button("Volver", variant="default", id="btn-volver")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one("#tabla-providers", DataTable)
        tabla.add_columns("Key", "Type", "Base URL", "API Key")
        self._refrescar()

    def _refrescar(self) -> None:
        """Recarga la lista de providers."""
        self._providers = self._container.list_providers.execute()
        tabla = self.query_one("#tabla-providers", DataTable)
        tabla.clear()
        for p in self._providers:
            api_key_str = "✓" if p.tiene_api_key else "—"
            tabla.add_row(
                p.key,
                p.type or "—",
                p.base_url or "—",
                api_key_str,
                key=p.key,
            )

    def _provider_seleccionado(self) -> "ProviderInfo | None":
        tabla = self.query_one("#tabla-providers", DataTable)
        row_key = tabla.cursor_row
        if row_key is None or row_key >= len(self._providers):
            return None
        return self._providers[row_key]

    def action_nuevo_provider(self) -> None:
        def _on_result(datos: dict | None) -> None:
            if datos is None:
                return
            try:
                self._container.upsert_provider.execute(
                    key=datos["key"],
                    type=datos["type"],
                    base_url=datos["base_url"],
                    api_key=datos["api_key"],
                )
                self._refrescar()
                self.notify(f"Provider '{datos['key']}' creado.", title="OK")
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")

        self.push_screen(_EditarProviderModal(es_nuevo=True), _on_result)

    def action_eliminar_provider(self) -> None:
        p = self._provider_seleccionado()
        if p is None:
            self.notify("Seleccioná un provider primero.", severity="warning")
            return

        def _on_confirm(resultado: str | None) -> None:
            if resultado is None:
                return
            try:
                borrar_api_key = resultado == "con_key"
                self._container.delete_provider.execute(p.key, borrar_api_key=borrar_api_key)
                self._refrescar()
                self.notify(f"Provider '{p.key}' eliminado.", title="OK")
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")

        self.push_screen(_ConfirmarEliminarModal(p.key, p.tiene_api_key), _on_confirm)

    def action_volver(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-nuevo":
            self.action_nuevo_provider()
        elif btn_id == "btn-editar":
            p = self._provider_seleccionado()
            if p is None:
                self.notify("Seleccioná un provider primero.", severity="warning")
                return

            def _on_edit(datos: dict | None) -> None:
                if datos is None:
                    return
                try:
                    self._container.upsert_provider.execute(
                        key=datos["key"],
                        type=datos["type"],
                        base_url=datos["base_url"],
                        api_key=datos["api_key"],
                    )
                    self._refrescar()
                    self.notify(f"Provider '{datos['key']}' actualizado.", title="OK")
                except Exception as e:
                    self.notify(f"Error: {e}", severity="error")

            self.push_screen(
                _EditarProviderModal(
                    key=p.key,
                    type=p.type or "",
                    base_url=p.base_url or "",
                    tiene_api_key=p.tiene_api_key,
                    es_nuevo=False,
                ),
                _on_edit,
            )
        elif btn_id == "btn-eliminar":
            self.action_eliminar_provider()
        elif btn_id == "btn-volver":
            self.action_volver()
