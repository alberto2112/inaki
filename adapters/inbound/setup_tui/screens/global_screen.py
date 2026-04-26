"""
GlobalScreen — menú de secciones de configuración global.

Muestra la lista de secciones editables del ``global.yaml``.
Al seleccionar una, navega a la subpantalla correspondiente.
No edita nada directamente.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer

from core.ports.config_repository import LayerName

# Mapa de clave → (descripción, clase de pantalla)
_SECCIONES: list[tuple[str, str]] = [
    ("app", "Nombre del asistente, log level, agente por defecto"),
    ("llm", "Provider, modelo, temperatura, max_tokens"),
    ("embedding", "Provider, modelo ONNX, dimensión, caché"),
    ("memory", "Memoria a largo plazo: DB, top_k, schedule"),
    ("memory.llm", "Override del LLM de consolidación de memoria"),
    ("chat_history", "Historial a corto plazo: DB, max_messages"),
    ("tools", "Selección de tools: routing, iterations, circuit breaker"),
    ("skills", "Selección de skills: routing, top_k, sticky"),
    ("semantic_routing", "Políticas transversales de routing"),
    ("workspace", "Directorio de trabajo del agente"),
    ("admin", "Admin server del daemon: host, port, timeout"),
    ("transcription", "Transcripción de audio: provider, modelo, idioma"),
    ("user", "Preferencias del usuario: timezone"),
    ("delegation", "Delegación agente-a-agente: max_iterations, timeout"),
    ("knowledge", "Fuentes de conocimiento externas (flags top-level)"),
]


class GlobalScreen(Screen):
    """Menú de secciones de configuración global."""

    BINDINGS = [
        Binding("escape", "cancelar", "Volver", show=True),
        Binding("enter", "seleccionar", "Editar sección", show=True),
    ]

    def __init__(self, container: "SetupContainer") -> None:
        super().__init__()
        self._container = container

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ScrollableContainer():
            yield Label(
                "[bold]Configuración Global — Seleccioná una sección para editar[/bold]",
                markup=True,
            )
            yield DataTable(id="tabla-secciones", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one("#tabla-secciones", DataTable)
        tabla.add_columns("Sección", "Descripción")
        for clave, descripcion in _SECCIONES:
            tabla.add_row(clave, descripcion, key=clave)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._abrir_seccion(str(event.row_key.value))

    def action_seleccionar(self) -> None:
        tabla = self.query_one("#tabla-secciones", DataTable)
        row_key = tabla.cursor_row
        if row_key is None or row_key >= len(_SECCIONES):
            return
        clave = _SECCIONES[row_key][0]
        self._abrir_seccion(clave)

    def _abrir_seccion(self, clave: str) -> None:
        """Navega a la subpantalla de la sección seleccionada."""
        pantalla = _resolver_pantalla_global(clave, self._container)
        if pantalla is not None:
            self.app.push_screen(pantalla)
        else:
            self.notify(f"Sección '{clave}': sin pantalla de edición asignada.", severity="warning")

    def action_cancelar(self) -> None:
        self.app.pop_screen()


def _resolver_pantalla_global(clave: str, container: "SetupContainer") -> Screen | None:
    """Instancia la pantalla correcta para la clave de sección dada."""
    from adapters.inbound.setup_tui.screens.sections.app_screen import AppScreen
    from adapters.inbound.setup_tui.screens.sections.llm_screen import LLMScreen
    from adapters.inbound.setup_tui.screens.sections.embedding_screen import EmbeddingScreen
    from adapters.inbound.setup_tui.screens.sections.memory_screen import MemoryScreen
    from adapters.inbound.setup_tui.screens.sections.memory_llm_screen import MemoryLLMScreen
    from adapters.inbound.setup_tui.screens.sections.chat_history_screen import ChatHistoryScreen
    from adapters.inbound.setup_tui.screens.sections.tools_screen import ToolsScreen
    from adapters.inbound.setup_tui.screens.sections.skills_screen import SkillsScreen
    from adapters.inbound.setup_tui.screens.sections.semantic_routing_screen import SemanticRoutingScreen
    from adapters.inbound.setup_tui.screens.sections.workspace_screen import WorkspaceScreen
    from adapters.inbound.setup_tui.screens.sections.admin_screen import AdminScreen
    from adapters.inbound.setup_tui.screens.sections.transcription_screen import TranscriptionScreen
    from adapters.inbound.setup_tui.screens.sections.user_screen import UserScreen
    from adapters.inbound.setup_tui.screens.sections.delegation_screen import DelegationScreen
    from adapters.inbound.setup_tui.screens.sections.knowledge_screen import KnowledgeScreen

    mapa: dict[str, type] = {
        "app": AppScreen,
        "llm": LLMScreen,
        "embedding": EmbeddingScreen,
        "memory": MemoryScreen,
        "memory.llm": MemoryLLMScreen,
        "chat_history": ChatHistoryScreen,
        "tools": ToolsScreen,
        "skills": SkillsScreen,
        "semantic_routing": SemanticRoutingScreen,
        "workspace": WorkspaceScreen,
        "admin": AdminScreen,
        "transcription": TranscriptionScreen,
        "user": UserScreen,
        "delegation": DelegationScreen,
        "knowledge": KnowledgeScreen,
    }

    clase = mapa.get(clave)
    if clase is None:
        return None

    if clave == "memory.llm":
        return clase(container, layer=LayerName.GLOBAL)
    return clase(container, layer=LayerName.GLOBAL)
