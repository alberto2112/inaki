"""
AgentsScreen — lista de agentes con acciones Crear / Clonar / Eliminar.

Crear: solo pide id, name, description, system_prompt.
Clonar: copia el YAML del agente origen a un nuevo id.
Eliminar: si tiene secrets.yaml, confirma si también eliminarlos.
Editar: abre AgentEditorScreen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer

from core.domain.errors import AgentYaExisteError
from core.ports.config_repository import LayerName


# ---------------------------------------------------------------------------
# Modal de creación de agente
# ---------------------------------------------------------------------------


class _CrearAgenteModal(ModalScreen[dict | None]):
    """Modal para crear un nuevo agente (campos mínimos)."""

    CSS = """
    _CrearAgenteModal {
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

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[bold]Nuevo agente[/bold]", markup=True)
            yield Label("ID (slug, sin espacios):")
            yield Input(placeholder="ej: asistente", id="input-id")
            yield Label("Nombre:")
            yield Input(placeholder="ej: Asistente General", id="input-name")
            yield Label("Descripción (opcional):")
            yield Input(id="input-desc")
            yield Label("System prompt:")
            yield Input(value="Sos un asistente de IA.", id="input-prompt")
            with Horizontal():
                yield Button("Crear", variant="primary", id="btn-crear")
                yield Button("Cancelar", variant="default", id="btn-cancelar")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-crear":
            agent_id = self.query_one("#input-id", Input).value.strip()
            if not agent_id:
                self.notify("El ID no puede estar vacío.", severity="error")
                return
            self.dismiss({
                "id": agent_id,
                "name": self.query_one("#input-name", Input).value.strip(),
                "desc": self.query_one("#input-desc", Input).value.strip(),
                "prompt": self.query_one("#input-prompt", Input).value.strip(),
            })
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal de clonar agente
# ---------------------------------------------------------------------------


class _ClonarAgenteModal(ModalScreen[str | None]):
    """Modal para ingresar el ID del nuevo agente clonado."""

    def __init__(self, origen_id: str) -> None:
        super().__init__()
        self._origen_id = origen_id

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Clonar [bold]{self._origen_id}[/bold]", markup=True)
            yield Label("Nuevo ID:")
            yield Input(placeholder="ej: asistente-2", id="input-nuevo-id")
            with Horizontal():
                yield Button("Clonar", variant="primary", id="btn-clonar")
                yield Button("Cancelar", variant="default", id="btn-cancelar")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-clonar":
            nuevo_id = self.query_one("#input-nuevo-id", Input).value.strip()
            if not nuevo_id:
                self.notify("El ID no puede estar vacío.", severity="error")
                return
            self.dismiss(nuevo_id)
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal de confirmación de eliminación
# ---------------------------------------------------------------------------


class _ConfirmarEliminarAgenteModal(ModalScreen[str | None]):
    """Modal para confirmar eliminación de agente con opción de borrar secrets."""

    def __init__(self, agent_id: str, tiene_secrets: bool) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._tiene_secrets = tiene_secrets

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"¿Eliminar agente [bold]{self._agent_id}[/bold]?", markup=True)
            if self._tiene_secrets:
                yield Label(
                    "[yellow]Este agente tiene un archivo de secrets.[/yellow]",
                    markup=True,
                )
                yield Button("Eliminar + borrar secrets", variant="error", id="btn-todo")
                yield Button("Eliminar (mantener secrets)", variant="warning", id="btn-solo-yaml")
            else:
                yield Button("Eliminar", variant="error", id="btn-solo-yaml")
            yield Button("Cancelar", variant="default", id="btn-cancelar")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-todo":
            self.dismiss("con_secrets")
        elif event.button.id == "btn-solo-yaml":
            self.dismiss("sin_secrets")
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Pantalla principal
# ---------------------------------------------------------------------------


class AgentsScreen(Screen):
    """Lista de agentes con CRUD completo."""

    BINDINGS = [
        Binding("n", "crear_agente", "Crear", show=True),
        Binding("c", "clonar_agente", "Clonar", show=True),
        Binding("delete", "eliminar_agente", "Eliminar", show=True),
        Binding("enter", "editar_agente", "Editar", show=True),
        Binding("escape", "volver", "Volver", show=True),
    ]

    def __init__(self, container: "SetupContainer") -> None:
        super().__init__()
        self._container = container
        self._agentes: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ScrollableContainer():
            yield Label("[bold]Agentes[/bold]", markup=True)
            yield DataTable(id="tabla-agentes")
            with Static():
                yield Button("Crear (N)", variant="primary", id="btn-crear")
                yield Button("Clonar (C)", variant="default", id="btn-clonar")
                yield Button("Editar (Enter)", variant="default", id="btn-editar")
                yield Button("Eliminar (Del)", variant="error", id="btn-eliminar")
                yield Button("Volver", variant="default", id="btn-volver")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one("#tabla-agentes", DataTable)
        tabla.add_columns("ID", "Secrets")
        self._refrescar()

    def _refrescar(self) -> None:
        self._agentes = self._container.list_agents.execute()
        tabla = self.query_one("#tabla-agentes", DataTable)
        tabla.clear()
        for ag_id in self._agentes:
            tiene_secrets = self._container.repo.layer_exists(
                LayerName.AGENT_SECRETS, agent_id=ag_id
            )
            tabla.add_row(ag_id, "✓" if tiene_secrets else "—", key=ag_id)

    def _agente_seleccionado(self) -> str | None:
        tabla = self.query_one("#tabla-agentes", DataTable)
        row = tabla.cursor_row
        if row is None or row >= len(self._agentes):
            return None
        return self._agentes[row]

    def action_crear_agente(self) -> None:
        def _on_result(datos: dict | None) -> None:
            if datos is None:
                return
            try:
                self._container.create_agent.execute(
                    agent_id=datos["id"],
                    nombre=datos["name"],
                    descripcion=datos["desc"],
                    system_prompt=datos["prompt"],
                )
                self._refrescar()
                self.notify(f"Agente '{datos['id']}' creado.", title="OK")
            except AgentYaExisteError:
                self.notify(f"Ya existe un agente con ID '{datos['id']}'.", severity="error")
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")

        self.push_screen(_CrearAgenteModal(), _on_result)

    def action_clonar_agente(self) -> None:
        origen_id = self._agente_seleccionado()
        if origen_id is None:
            self.notify("Seleccioná un agente primero.", severity="warning")
            return

        def _on_result(nuevo_id: str | None) -> None:
            if nuevo_id is None:
                return
            try:
                datos_origen = self._container.repo.read_layer(
                    LayerName.AGENT, agent_id=origen_id
                )
                datos_clone = dict(datos_origen)
                datos_clone["id"] = nuevo_id
                # Verificar que no exista
                if self._container.repo.layer_exists(LayerName.AGENT, agent_id=nuevo_id):
                    raise AgentYaExisteError(nuevo_id)
                self._container.repo.write_layer(LayerName.AGENT, datos_clone, agent_id=nuevo_id)
                self._refrescar()
                self.notify(f"Agente '{nuevo_id}' clonado desde '{origen_id}'.", title="OK")
            except AgentYaExisteError:
                self.notify(f"Ya existe un agente con ID '{nuevo_id}'.", severity="error")
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")

        self.push_screen(_ClonarAgenteModal(origen_id), _on_result)

    def action_eliminar_agente(self) -> None:
        ag_id = self._agente_seleccionado()
        if ag_id is None:
            self.notify("Seleccioná un agente primero.", severity="warning")
            return

        tiene_secrets = self._container.repo.layer_exists(
            LayerName.AGENT_SECRETS, agent_id=ag_id
        )

        def _on_confirm(resultado: str | None) -> None:
            if resultado is None:
                return
            try:
                self._container.delete_agent.execute(ag_id)
                if resultado == "con_secrets":
                    self._container.delete_agent.execute_secrets(ag_id)
                self._refrescar()
                self.notify(f"Agente '{ag_id}' eliminado.", title="OK")
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")

        self.push_screen(_ConfirmarEliminarAgenteModal(ag_id, tiene_secrets), _on_confirm)

    def action_editar_agente(self) -> None:
        ag_id = self._agente_seleccionado()
        if ag_id is None:
            return
        from adapters.inbound.setup_tui.screens.agent_editor_screen import AgentEditorScreen

        self.push_screen(AgentEditorScreen(self._container, ag_id))

    def action_volver(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-crear":
            self.action_crear_agente()
        elif btn_id == "btn-clonar":
            self.action_clonar_agente()
        elif btn_id == "btn-editar":
            self.action_editar_agente()
        elif btn_id == "btn-eliminar":
            self.action_eliminar_agente()
        elif btn_id == "btn-volver":
            self.action_volver()
