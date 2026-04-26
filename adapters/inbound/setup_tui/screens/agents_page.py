"""AgentsPage — lista y gestión de agentes (crear / editar / clonar / eliminar)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.modals._dialog import dialog_css
from adapters.inbound.setup_tui.screens._base import BasePage
from adapters.inbound.setup_tui.widgets.config_row import ConfigRow
from adapters.inbound.setup_tui.widgets.section_header import SectionHeader

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer


# ---------------------------------------------------------------------------
# Modal: crear agente nuevo
# ---------------------------------------------------------------------------


class _CreateAgentModal(ModalScreen[dict[str, str] | None]):
    """Modal de 4 campos para crear un nuevo agente.

    Retorna un dict con keys ``id``, ``name``, ``description``, ``system_prompt``,
    o ``None`` si el usuario cancela.
    """

    DEFAULT_CSS = (
        dialog_css("_CreateAgentModal")
        + """
    _CreateAgentModal #dialog {
        width: 78;
        max-height: 28;
    }
    _CreateAgentModal #dialog Input {
        margin-top: 0;
        background: #0d0d0d;
        border: tall $primary;
    }
    _CreateAgentModal #dialog .campo-label {
        height: 1;
        margin-top: 1;
        color: $text-muted;
        text-style: dim;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("ctrl+s", "commit", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("nuevo agente", classes="titulo")
            yield Label("id  [dim](slug, sin espacios)[/dim]", classes="campo-label")
            yield Input(placeholder="ej: dev, planner, ops", id="input_id")
            yield Label("nombre", classes="campo-label")
            yield Input(placeholder="nombre legible", id="input_name")
            yield Label("descripción", classes="campo-label")
            yield Input(placeholder="breve descripción", id="input_desc")
            yield Label("system prompt", classes="campo-label")
            yield Input(
                placeholder="Sos un asistente de IA.",
                id="input_system",
            )
            yield Label(
                "[bold]ctrl+s[/bold] [dim]guardar[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def on_mount(self) -> None:
        self.query_one("#input_id", Input).focus()

    def action_commit(self) -> None:
        agent_id = self.query_one("#input_id", Input).value.strip()
        if not agent_id:
            self.app.notify("el id del agente no puede estar vacío", severity="warning", timeout=2)
            return
        self.dismiss(
            {
                "id": agent_id,
                "name": self.query_one("#input_name", Input).value.strip(),
                "description": self.query_one("#input_desc", Input).value.strip(),
                "system_prompt": self.query_one("#input_system", Input).value.strip(),
            }
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal: clonar agente
# ---------------------------------------------------------------------------


class _CloneAgentModal(ModalScreen[str | None]):
    """Modal que pide el nuevo id para clonar un agente existente.

    Retorna el nuevo id (str) o ``None`` si el usuario cancela.
    """

    DEFAULT_CSS = (
        dialog_css("_CloneAgentModal")
        + """
    _CloneAgentModal #dialog Input {
        margin-top: 1;
        background: #0d0d0d;
        border: tall $primary;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, origen_id: str) -> None:
        super().__init__()
        self._origen_id = origen_id

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"clonar  [bold]{self._origen_id}[/bold]  → nuevo id",
                classes="titulo",
            )
            yield Input(placeholder="nuevo-id", id="input_nuevo_id")
            yield Label(
                "[bold]enter[/bold] [dim]clonar[/dim]   "
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def on_mount(self) -> None:
        self.query_one("#input_nuevo_id", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        nuevo_id = event.value.strip()
        if not nuevo_id:
            self.app.notify("el id no puede estar vacío", severity="warning", timeout=2)
            return
        self.dismiss(nuevo_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal: confirmación de eliminación
# ---------------------------------------------------------------------------


class _ConfirmDeleteAgentModal(ModalScreen[str | None]):
    """Modal de confirmación para eliminar un agente.

    Retorna ``"solo_yaml"`` para eliminar solo el YAML principal,
    ``"con_secrets"`` para eliminar YAML + secrets, o ``None`` para cancelar.
    """

    DEFAULT_CSS = (
        dialog_css("_ConfirmDeleteAgentModal")
        + """
    _ConfirmDeleteAgentModal #dialog {
        width: 70;
    }
    _ConfirmDeleteAgentModal .opcion {
        height: 1;
        margin-top: 1;
        color: $text;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("y", "solo_yaml", show=False),
        Binding("s", "con_secrets", show=False),
    ]

    def __init__(self, agent_id: str, tiene_secrets: bool) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._tiene_secrets = tiene_secrets

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"eliminar  [bold]{self._agent_id}[/bold]",
                classes="titulo",
            )
            yield Label(
                "[bold]y[/bold]  [dim]eliminar solo agents/{id}.yaml[/dim]",
                classes="opcion",
            )
            if self._tiene_secrets:
                yield Label(
                    "[bold]s[/bold]  [dim]eliminar yaml + secrets.yaml[/dim]",
                    classes="opcion",
                )
            yield Label(
                "[bold]esc[/bold] [dim]cancelar[/dim]",
                classes="footer",
            )

    def action_solo_yaml(self) -> None:
        self.dismiss("solo_yaml")

    def action_con_secrets(self) -> None:
        self.dismiss("con_secrets")

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# AgentsPage
# ---------------------------------------------------------------------------


class AgentsPage(BasePage):
    """Página de gestión de agentes: lista, crear, clonar, eliminar.

    Cada agente se muestra como una fila. Enter navega al detalle del agente.
    ``n`` crea uno nuevo, ``c`` clona el seleccionado, ``delete`` lo elimina.
    """

    BINDINGS = BasePage.BINDINGS + [
        Binding("n", "create_agent", description="nuevo", show=True, priority=True),
        Binding("c", "clone_agent", description="clonar", show=True, priority=True),
        Binding("delete", "delete_agent", description="eliminar", show=True, priority=True),
    ]

    def __init__(self, container: "SetupContainer | None", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._container = container

    def breadcrumb(self) -> str:
        return "inaki / config / agents"

    def compose_body(self) -> ComposeResult:
        agentes: list[str] = []
        if self._container is not None:
            try:
                agentes = self._container.list_agents.execute()
            except Exception:
                pass

        yield SectionHeader("AGENTS")

        if not agentes:
            # Fila placeholder para que el cursor tenga algo donde pararse
            yield ConfigRow(
                Field(
                    label="(sin agentes)",
                    value="→ presioná n para crear uno",
                    kind="scalar",
                )
            )
        else:
            for agent_id in agentes:
                yield ConfigRow(
                    Field(
                        label=agent_id,
                        value="→",
                        kind="scalar",
                    )
                )

    def action_edit(self) -> None:
        """En lugar de abrir un modal, navega al detalle del agente seleccionado."""
        if not self._fields:
            return

        field = self._current_field()
        agent_id = field.label

        # Si es la fila placeholder, no hacer nada útil
        if agent_id == "(sin agentes)":
            return

        from adapters.inbound.setup_tui.screens.agent_detail_page import AgentDetailPage

        self.app.push_screen(AgentDetailPage(self._container, agent_id))

    def action_create_agent(self) -> None:
        """Abre el modal de creación y crea el agente si el usuario confirma."""
        self.app.push_screen(_CreateAgentModal(), self._after_create)

    def _after_create(self, datos: dict[str, str] | None) -> None:
        if datos is None or self._container is None:
            return

        try:
            self._container.create_agent.execute(
                agent_id=datos["id"],
                nombre=datos["name"],
                descripcion=datos["description"],
                system_prompt=datos["system_prompt"],
            )
            self.app.notify(
                f"agente '{datos['id']}' creado",
                title="agents",
                timeout=2,
            )
        except Exception as exc:
            self.app.notify(str(exc), title="error al crear", severity="error", timeout=4)
            return

        # Refrescar la página
        self._reload()

    def action_clone_agent(self) -> None:
        """Clona el agente seleccionado en un nuevo id."""
        if not self._fields:
            return

        agent_id = self._current_field().label
        if agent_id == "(sin agentes)":
            return

        self.app.push_screen(_CloneAgentModal(agent_id), lambda nuevo_id: self._after_clone(agent_id, nuevo_id))

    def _after_clone(self, origen_id: str, nuevo_id: str | None) -> None:
        if nuevo_id is None or self._container is None:
            return

        try:
            from core.ports.config_repository import LayerName

            # Leer la capa del agente origen
            datos = self._container.repo.read_layer(LayerName.AGENT, agent_id=origen_id)
            # Actualizar el id en los datos clonados
            if isinstance(datos, dict):
                datos = dict(datos)
                datos["id"] = nuevo_id
            # Escribir la nueva capa
            self._container.repo.write_layer(LayerName.AGENT, datos, agent_id=nuevo_id)
            self.app.notify(
                f"agente '{origen_id}' clonado como '{nuevo_id}'",
                title="agents",
                timeout=2,
            )
        except Exception as exc:
            self.app.notify(str(exc), title="error al clonar", severity="error", timeout=4)
            return

        self._reload()

    def action_delete_agent(self) -> None:
        """Solicita confirmación y elimina el agente seleccionado."""
        if not self._fields:
            return

        agent_id = self._current_field().label
        if agent_id == "(sin agentes)":
            return

        if self._container is None:
            return

        from core.ports.config_repository import LayerName

        tiene_secrets = self._container.repo.layer_exists(
            LayerName.AGENT_SECRETS, agent_id=agent_id
        )

        self.app.push_screen(
            _ConfirmDeleteAgentModal(agent_id, tiene_secrets),
            lambda resultado: self._after_delete(agent_id, resultado),
        )

    def _after_delete(self, agent_id: str, resultado: str | None) -> None:
        if resultado is None or self._container is None:
            return

        try:
            self._container.delete_agent.execute(agent_id)
            if resultado == "con_secrets":
                self._container.delete_agent.execute_secrets(agent_id)
            self.app.notify(
                f"agente '{agent_id}' eliminado",
                title="agents",
                timeout=2,
            )
        except Exception as exc:
            self.app.notify(str(exc), title="error al eliminar", severity="error", timeout=4)
            return

        self._reload()

    def _reload(self) -> None:
        """Vuelve al stack anterior y re-pushea esta página para refrescar la lista."""
        # Reemplazar la pantalla actual con una instancia nueva
        self.app.pop_screen()
        self.app.push_screen(AgentsPage(self._container))
