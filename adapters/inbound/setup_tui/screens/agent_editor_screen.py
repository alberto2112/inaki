"""
AgentEditorScreen — menú de edición de un agente individual.

Layout:
  1. Campos básicos inline: id, name, description, system_prompt, memory.enabled.
  2. Lista de secciones de override: al seleccionar, navega a la sub-pantalla.
  3. Channels: enlace a ChannelsScreen (ya funcionaba).

Al cargar, si el YAML tiene ``broadcast.port`` Y ``broadcast.remote.host``
simultáneamente, muestra modal de elección (UX-decision#3).

Los helpers de broadcast se importan desde ``_broadcast_helpers`` para
que los tests existentes sigan funcionando vía re-export.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from adapters.inbound.setup_tui.screens._broadcast_helpers import (
    _BroadcastAmbiguoModal,
    detectar_broadcast_ambiguo,
    resolver_broadcast_client,
    resolver_broadcast_server,
)
from adapters.inbound.setup_tui.widgets.diff_preview import DiffPreview

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer

from core.ports.config_repository import LayerName
from core.use_cases.config.update_agent_layer import CampoTriestado, TristadoValor

# Re-exportar para que los tests de import desde este módulo sigan funcionando
__all__ = [
    "AgentEditorScreen",
    "detectar_broadcast_ambiguo",
    "resolver_broadcast_server",
    "resolver_broadcast_client",
    "_BroadcastAmbiguoModal",
]

# Secciones de override disponibles para el agente
_SECCIONES_OVERRIDE: list[tuple[str, str]] = [
    ("llm", "Override de provider, modelo, temperatura"),
    ("embedding", "Override de embedding"),
    ("memory", "Override de memoria: enabled, top_k"),
    ("memory.llm", "Override del LLM de consolidación (tri-estado)"),
    ("chat_history", "Override del historial a corto plazo"),
    ("tools", "Override de tools"),
    ("skills", "Override de skills"),
    ("semantic_routing", "Override de semantic routing"),
    ("workspace", "Override de workspace"),
    ("transcription", "Override de transcripción"),
    ("delegation", "Override de delegación"),
    ("knowledge", "Override de knowledge"),
    ("providers", "Overrides de api_key por provider"),
    ("channels", "Canales (Telegram, REST, broadcast)"),
]


class AgentEditorScreen(Screen):
    """Menú de edición de un agente con campos básicos inline y sub-screens de override."""

    BINDINGS = [
        Binding("ctrl+s", "guardar_basicos", "Guardar básicos", show=True),
        Binding("escape", "cancelar", "Volver", show=True),
        Binding("enter", "seleccionar_seccion", "Editar sección", show=True),
    ]

    def __init__(self, container: "SetupContainer", agent_id: str) -> None:
        super().__init__()
        self._container = container
        self._agent_id = agent_id
        self._datos_en_memoria: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ScrollableContainer():
            yield Label(
                f"[bold]Agente: {self._agent_id}[/bold]",
                markup=True,
            )

            # Campos básicos inline
            yield Label("[bold]Campos básicos[/bold]", markup=True)
            yield Label("[dim]Ctrl+S para guardar los campos básicos.[/dim]", markup=True)
            with Vertical(id="campos-basicos"):
                for campo in ("id", "name", "description"):
                    yield Label(f"{campo}:")
                    yield Input(placeholder=campo, id=f"input-{campo}")
                yield Label("system_prompt:")
                yield Input(placeholder="Prompt del sistema", id="input-system_prompt")
                yield Label("memory.enabled:")
                yield Input(placeholder="true / false", id="input-memory-enabled")

            yield Button("Guardar campos básicos (Ctrl+S)", variant="primary", id="btn-guardar-basicos")

            # Secciones de override
            yield Label(
                "[bold]Secciones de configuración — seleccioná para editar[/bold]",
                markup=True,
            )
            yield DataTable(id="tabla-secciones", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one("#tabla-secciones", DataTable)
        tabla.add_columns("Sección", "Descripción")
        for clave, descripcion in _SECCIONES_OVERRIDE:
            tabla.add_row(clave, descripcion, key=clave)
        self._cargar_agente()

    def _cargar_agente(self) -> None:
        """Carga la config del agente. Si hay broadcast ambiguo, muestra modal."""
        datos_capa = self._container.repo.read_layer(
            LayerName.AGENT, agent_id=self._agent_id
        )

        if detectar_broadcast_ambiguo(datos_capa):
            def _on_modal(eleccion: str | None) -> None:
                if eleccion is None:
                    self.app.pop_screen()
                    return
                if eleccion == "server":
                    self._datos_en_memoria = resolver_broadcast_server(datos_capa)
                else:
                    self._datos_en_memoria = resolver_broadcast_client(datos_capa)
                self._poblar_campos_basicos()

            self.push_screen(_BroadcastAmbiguoModal(), _on_modal)
        else:
            self._datos_en_memoria = dict(datos_capa)
            self._poblar_campos_basicos()

    def _poblar_campos_basicos(self) -> None:
        datos = self._datos_en_memoria
        for campo in ("id", "name", "description", "system_prompt"):
            try:
                inp = self.query_one(f"#input-{campo}", Input)
                inp.value = str(datos.get(campo, "") or "")
            except Exception:
                pass

        # memory.enabled
        memory_datos = datos.get("memory") or {}
        enabled = memory_datos.get("enabled", True)
        try:
            inp = self.query_one("#input-memory-enabled", Input)
            inp.value = "true" if enabled else "false"
        except Exception:
            pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._abrir_seccion(str(event.row_key.value))

    def action_seleccionar_seccion(self) -> None:
        tabla = self.query_one("#tabla-secciones", DataTable)
        row_idx = tabla.cursor_row
        if row_idx is None or row_idx >= len(_SECCIONES_OVERRIDE):
            return
        clave = _SECCIONES_OVERRIDE[row_idx][0]
        self._abrir_seccion(clave)

    def _abrir_seccion(self, clave: str) -> None:
        """Navega a la subpantalla de override correspondiente."""
        pantalla = _resolver_pantalla_agente(clave, self._container, self._agent_id)
        if pantalla is not None:
            self.app.push_screen(pantalla)
        else:
            self.notify(f"Sección '{clave}': sin pantalla asignada.", severity="warning")

    async def action_guardar_basicos(self) -> None:
        await self._guardar_basicos()

    async def _guardar_basicos(self) -> None:
        """Guarda los campos básicos del agente (id, name, description, system_prompt, memory.enabled)."""
        cambios: dict[str, Any] = {}

        for campo in ("id", "name", "description", "system_prompt"):
            try:
                inp = self.query_one(f"#input-{campo}", Input)
                valor_original = str(self._datos_en_memoria.get(campo, "") or "")
                if inp.value != valor_original:
                    cambios[campo] = inp.value
            except Exception:
                pass

        # memory.enabled
        try:
            inp = self.query_one("#input-memory-enabled", Input)
            enabled_nuevo = inp.value.strip().lower() in ("true", "1", "yes", "sí", "si")
            memory_datos = self._datos_en_memoria.get("memory") or {}
            enabled_original = bool(memory_datos.get("enabled", True))
            if enabled_nuevo != enabled_original:
                cambios["memory"] = {"enabled": enabled_nuevo}
        except Exception:
            pass

        if not cambios:
            self.notify("Sin cambios en los campos básicos.", title="Info")
            return

        try:
            self._container.update_agent_layer.execute(
                agent_id=self._agent_id,
                cambios=cambios,
                layer=LayerName.AGENT,
            )
            # Actualizar snapshot en memoria
            for k, v in cambios.items():
                if k == "memory" and isinstance(v, dict):
                    self._datos_en_memoria.setdefault("memory", {}).update(v)
                else:
                    self._datos_en_memoria[k] = v
            self.notify("Campos básicos guardados.", title="OK")
        except Exception as e:
            self.notify(f"Error al guardar: {e}", severity="error")

    def action_cancelar(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-guardar-basicos":
            self.run_worker(self._guardar_basicos())


def _resolver_pantalla_agente(
    clave: str, container: "SetupContainer", agent_id: str
) -> Screen | None:
    """Instancia la pantalla de override correcta para la clave de sección dada."""
    from adapters.inbound.setup_tui.screens.channels_screen import ChannelsScreen
    from adapters.inbound.setup_tui.screens.sections.agent_chat_history_screen import AgentChatHistoryScreen
    from adapters.inbound.setup_tui.screens.sections.agent_delegation_screen import AgentDelegationScreen
    from adapters.inbound.setup_tui.screens.sections.agent_embedding_screen import AgentEmbeddingScreen
    from adapters.inbound.setup_tui.screens.sections.agent_knowledge_screen import AgentKnowledgeScreen
    from adapters.inbound.setup_tui.screens.sections.agent_llm_screen import AgentLLMScreen
    from adapters.inbound.setup_tui.screens.sections.agent_memory_llm_screen import AgentMemoryLLMScreen
    from adapters.inbound.setup_tui.screens.sections.agent_memory_screen import AgentMemoryScreen
    from adapters.inbound.setup_tui.screens.sections.agent_providers_screen import AgentProvidersScreen
    from adapters.inbound.setup_tui.screens.sections.agent_semantic_routing_screen import (
        AgentSemanticRoutingScreen,
    )
    from adapters.inbound.setup_tui.screens.sections.agent_skills_screen import AgentSkillsScreen
    from adapters.inbound.setup_tui.screens.sections.agent_tools_screen import AgentToolsScreen
    from adapters.inbound.setup_tui.screens.sections.agent_transcription_screen import (
        AgentTranscriptionScreen,
    )
    from adapters.inbound.setup_tui.screens.sections.agent_workspace_screen import AgentWorkspaceScreen

    if clave == "channels":
        return ChannelsScreen(container, agent_id=agent_id)

    if clave == "providers":
        return AgentProvidersScreen(container, agent_id=agent_id)

    # Secciones genéricas con override_mode=True
    clases_override: dict[str, type] = {
        "llm": AgentLLMScreen,
        "embedding": AgentEmbeddingScreen,
        "memory": AgentMemoryScreen,
        "memory.llm": AgentMemoryLLMScreen,
        "chat_history": AgentChatHistoryScreen,
        "tools": AgentToolsScreen,
        "skills": AgentSkillsScreen,
        "semantic_routing": AgentSemanticRoutingScreen,
        "workspace": AgentWorkspaceScreen,
        "transcription": AgentTranscriptionScreen,
        "delegation": AgentDelegationScreen,
        "knowledge": AgentKnowledgeScreen,
    }

    clase = clases_override.get(clave)
    if clase is None:
        return None
    return clase(container, layer=LayerName.AGENT, agent_id=agent_id, override_mode=True)
