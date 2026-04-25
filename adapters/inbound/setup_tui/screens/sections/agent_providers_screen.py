"""
Pantalla CRUD de providers a nivel agente.

Permite al agente rotar o sobreescribir api_key de un provider específico
sin tocar el registry global. La estructura es:

  providers:
    groq: { api_key: "gsk_agent_specific..." }

Solo se guarda la capa ``agent`` (no secrets). Para tokens sensibles,
se recomienda usar la pantalla Secrets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from adapters.inbound.setup_tui.widgets.masked_input import MaskedInput

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer

from core.ports.config_repository import LayerName


class _EditarProviderAgenteModal(ModalScreen[dict | None]):
    """Modal para crear/editar un override de provider en el agente."""

    CSS = """
    _EditarProviderAgenteModal {
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
        base_url: str = "",
        es_nuevo: bool = True,
    ) -> None:
        super().__init__()
        self._key = key
        self._base_url = base_url
        self._es_nuevo = es_nuevo

    def compose(self) -> ComposeResult:
        titulo = "Nuevo override de provider" if self._es_nuevo else f"Editar override: {self._key}"
        with Vertical(id="dialog"):
            yield Label(f"[bold]{titulo}[/bold]", markup=True)
            yield Label("Key del provider (debe existir en el registry global):")
            yield Input(value=self._key, id="input-key", disabled=not self._es_nuevo)
            yield Label("Base URL (opcional, para override del endpoint):")
            yield Input(value=self._base_url, id="input-base-url")
            yield Label("API Key del agente (vacío = no modificar):")
            yield MaskedInput(valor="", id="input-api-key")
            with Horizontal():
                yield Button("Guardar", variant="primary", id="btn-guardar")
                yield Button("Cancelar", variant="default", id="btn-cancelar")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-guardar":
            key_input = self.query_one("#input-key", Input)
            base_url_input = self.query_one("#input-base-url", Input)
            api_key_widget = self.query_one("#input-api-key", MaskedInput)

            key = key_input.value.strip()
            if not key:
                self.notify("La key no puede estar vacía.", severity="error")
                return

            self.dismiss({
                "key": key,
                "base_url": base_url_input.value.strip() or None,
                "api_key": api_key_widget.valor.strip() or None,
            })
        elif event.button.id == "btn-cancelar":
            self.dismiss(None)


class AgentProvidersScreen(Screen):
    """
    CRUD de overrides de providers del registry a nivel agente.

    Permite que un agente use una api_key distinta a la del global
    para un provider específico. Útil para rotar credenciales por agente.
    """

    BINDINGS = [
        Binding("n", "nuevo_override", "Nuevo", show=True),
        Binding("delete", "eliminar_override", "Eliminar", show=True),
        Binding("escape", "cancelar", "Volver", show=True),
    ]

    def __init__(self, container: "SetupContainer", agent_id: str) -> None:
        super().__init__()
        self._container = container
        self._agent_id = agent_id
        self._overrides: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ScrollableContainer():
            yield Label(
                f"[bold]Providers — Override de agente: {self._agent_id}[/bold]",
                markup=True,
            )
            yield Label(
                "[dim]Estos overrides tienen prioridad sobre el registry global "
                "para este agente.[/dim]",
                markup=True,
            )
            yield DataTable(id="tabla-overrides")
            with Static():
                yield Button("Nuevo (N)", variant="primary", id="btn-nuevo")
                yield Button("Eliminar (Del)", variant="error", id="btn-eliminar")
                yield Button("Volver", variant="default", id="btn-volver")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one("#tabla-overrides", DataTable)
        tabla.add_columns("Key", "Base URL", "API Key")
        self._refrescar()

    def _refrescar(self) -> None:
        datos_capa = self._container.repo.read_layer(LayerName.AGENT, agent_id=self._agent_id)
        self._overrides = datos_capa.get("providers") or {}
        tabla = self.query_one("#tabla-overrides", DataTable)
        tabla.clear()
        for key, cfg in self._overrides.items():
            if not isinstance(cfg, dict):
                continue
            api_key_str = "✓" if cfg.get("api_key") else "—"
            tabla.add_row(
                key,
                cfg.get("base_url") or "—",
                api_key_str,
                key=key,
            )

    def _override_seleccionado(self) -> str | None:
        tabla = self.query_one("#tabla-overrides", DataTable)
        row_idx = tabla.cursor_row
        keys = list(self._overrides.keys())
        if row_idx is None or row_idx >= len(keys):
            return None
        return keys[row_idx]

    def action_nuevo_override(self) -> None:
        def _on_result(datos: dict | None) -> None:
            if datos is None:
                return
            entry: dict[str, Any] = {}
            if datos.get("api_key"):
                entry["api_key"] = datos["api_key"]
            if datos.get("base_url"):
                entry["base_url"] = datos["base_url"]
            try:
                self._container.update_agent_layer.execute(
                    agent_id=self._agent_id,
                    cambios={"providers": {datos["key"]: entry}},
                    layer=LayerName.AGENT,
                )
                self._refrescar()
                self.notify(f"Override '{datos['key']}' guardado.", title="OK")
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")

        self.push_screen(_EditarProviderAgenteModal(es_nuevo=True), _on_result)

    def action_eliminar_override(self) -> None:
        key = self._override_seleccionado()
        if key is None:
            self.notify("Seleccioná un override primero.", severity="warning")
            return
        try:
            datos_capa = self._container.repo.read_layer(LayerName.AGENT, agent_id=self._agent_id)
            providers = dict(datos_capa.get("providers") or {})
            providers.pop(key, None)
            self._container.update_agent_layer.execute(
                agent_id=self._agent_id,
                cambios={"providers": providers},
                layer=LayerName.AGENT,
            )
            self._refrescar()
            self.notify(f"Override '{key}' eliminado.", title="OK")
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    def action_cancelar(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-nuevo":
            self.action_nuevo_override()
        elif btn_id == "btn-eliminar":
            self.action_eliminar_override()
        elif btn_id == "btn-volver":
            self.action_cancelar()
