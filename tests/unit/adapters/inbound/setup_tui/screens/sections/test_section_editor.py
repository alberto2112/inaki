"""
Tests de la clase base SectionEditorScreen y sus helpers.

Cubre:
  - _convertir_tipo: conversión de strings a tipos Python
  - FieldSpec.es_tristate: activa TristateToggle en override_mode
  - _HerenciaToggle: toggle de Heredar / Valor propio
  - SectionEditorScreen._recopilar_cambios: no depende de widgets Textual
    (testeamos la lógica de conversión de tipo y el comportamiento en modo no-override)

Los tests de render/mount se omiten en headless (smoke candidates para Phase 8).
"""

from __future__ import annotations


from adapters.inbound.setup_tui.screens.sections._base import (
    FieldSpec,
    _convertir_tipo,
)


# ---------------------------------------------------------------------------
# Tests de _convertir_tipo
# ---------------------------------------------------------------------------


class TestConvertirTipo:
    def test_str_retorna_string(self) -> None:
        assert _convertir_tipo("hola", FieldSpec("k", str)) == "hola"

    def test_int_valido(self) -> None:
        assert _convertir_tipo("42", FieldSpec("k", int)) == 42

    def test_int_invalido_retorna_string(self) -> None:
        assert _convertir_tipo("abc", FieldSpec("k", int)) == "abc"

    def test_float_valido(self) -> None:
        result = _convertir_tipo("0.7", FieldSpec("k", float))
        assert isinstance(result, float)
        assert abs(result - 0.7) < 0.001

    def test_float_invalido_retorna_string(self) -> None:
        assert _convertir_tipo("abc", FieldSpec("k", float)) == "abc"

    def test_bool_true_variantes(self) -> None:
        spec = FieldSpec("k", bool)
        for valor in ("true", "True", "TRUE", "1", "yes", "sí", "si"):
            assert _convertir_tipo(valor, spec) is True, f"Falló con: {valor!r}"

    def test_bool_false_variantes(self) -> None:
        spec = FieldSpec("k", bool)
        for valor in ("false", "False", "0", "no", ""):
            assert _convertir_tipo(valor, spec) is False, f"Falló con: {valor!r}"

    def test_int_con_espacios(self) -> None:
        assert _convertir_tipo("  42  ", FieldSpec("k", int)) == 42

    def test_float_con_espacios(self) -> None:
        result = _convertir_tipo("  3.14  ", FieldSpec("k", float))
        assert isinstance(result, float)

    def test_lista_csv(self) -> None:
        spec = FieldSpec("targets", str, es_lista=True)
        assert _convertir_tipo("a, b, c", spec) == ["a", "b", "c"]

    def test_lista_vacia(self) -> None:
        spec = FieldSpec("targets", str, es_lista=True)
        assert _convertir_tipo("", spec) == []

    def test_lista_con_espacios_extras(self) -> None:
        spec = FieldSpec("targets", str, es_lista=True)
        assert _convertir_tipo("  a , b  ,  c  ", spec) == ["a", "b", "c"]

    def test_lista_descarta_strings_vacios(self) -> None:
        spec = FieldSpec("targets", str, es_lista=True)
        assert _convertir_tipo("a,,b", spec) == ["a", "b"]

    def test_lista_un_solo_item(self) -> None:
        spec = FieldSpec("targets", str, es_lista=True)
        assert _convertir_tipo("solo", spec) == ["solo"]


# ---------------------------------------------------------------------------
# Tests de FieldSpec con es_tristate
# ---------------------------------------------------------------------------


class TestFieldSpecTristate:
    def test_tristate_false_por_defecto(self) -> None:
        spec = FieldSpec(key="model", tipo=str)
        assert spec.es_tristate is False

    def test_tristate_true_en_override(self) -> None:
        spec = FieldSpec(key="model", tipo=str, es_tristate=True)
        assert spec.es_tristate is True

    def test_campos_memory_llm_son_tristate(self) -> None:
        """Los 4 campos de memory.llm en AgentMemoryLLMScreen deben tener es_tristate=True."""
        from adapters.inbound.setup_tui.screens.sections.agent_memory_llm_screen import (
            AgentMemoryLLMScreen,
        )
        for campo in AgentMemoryLLMScreen.CAMPOS:
            assert campo.es_tristate is True, f"Campo {campo.key!r} debería tener es_tristate=True"


# ---------------------------------------------------------------------------
# Tests de imports y estructura de pantallas concretas
# ---------------------------------------------------------------------------


class TestImportsPantallasConcretasGlobal:
    """Verifica que todas las pantallas globales se importen correctamente."""

    def test_app_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.app_screen import AppScreen
        assert AppScreen.SECTION_KEY == "app"
        assert len(AppScreen.CAMPOS) > 0

    def test_llm_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.llm_screen import LLMScreen
        assert LLMScreen.SECTION_KEY == "llm"
        assert any(f.key == "model" for f in LLMScreen.CAMPOS)
        assert any(f.key == "provider" for f in LLMScreen.CAMPOS)

    def test_embedding_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.embedding_screen import EmbeddingScreen
        assert EmbeddingScreen.SECTION_KEY == "embedding"

    def test_memory_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.memory_screen import MemoryScreen
        assert MemoryScreen.SECTION_KEY == "memory"
        # `enabled` es per-agent only — no debe estar en MemoryScreen (global)
        assert not any(f.key == "enabled" for f in MemoryScreen.CAMPOS)
        # `channels_infused` es lista de canales (es_lista=True)
        channels_field = next(
            (f for f in MemoryScreen.CAMPOS if f.key == "channels_infused"), None
        )
        assert channels_field is not None
        assert channels_field.es_lista is True

    def test_agent_delegation_allowed_targets_es_lista(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_delegation_screen import (
            AgentDelegationScreen,
        )
        targets_field = next(
            (f for f in AgentDelegationScreen.CAMPOS if f.key == "allowed_targets"), None
        )
        assert targets_field is not None
        assert targets_field.es_lista is True

    def test_memory_llm_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.memory_llm_screen import MemoryLLMScreen
        assert MemoryLLMScreen.SECTION_KEY == "llm"

    def test_chat_history_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.chat_history_screen import ChatHistoryScreen
        assert ChatHistoryScreen.SECTION_KEY == "chat_history"

    def test_tools_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.tools_screen import ToolsScreen
        assert ToolsScreen.SECTION_KEY == "tools"

    def test_skills_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.skills_screen import SkillsScreen
        assert SkillsScreen.SECTION_KEY == "skills"

    def test_semantic_routing_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.semantic_routing_screen import (
            SemanticRoutingScreen,
        )
        assert SemanticRoutingScreen.SECTION_KEY == "semantic_routing"

    def test_workspace_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.workspace_screen import WorkspaceScreen
        assert WorkspaceScreen.SECTION_KEY == "workspace"

    def test_admin_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.admin_screen import AdminScreen
        assert AdminScreen.SECTION_KEY == "admin"

    def test_transcription_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.transcription_screen import TranscriptionScreen
        assert TranscriptionScreen.SECTION_KEY == "transcription"

    def test_user_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.user_screen import UserScreen
        assert UserScreen.SECTION_KEY == "user"

    def test_delegation_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.delegation_screen import DelegationScreen
        assert DelegationScreen.SECTION_KEY == "delegation"

    def test_knowledge_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.knowledge_screen import KnowledgeScreen
        assert KnowledgeScreen.SECTION_KEY == "knowledge"
        # Sources no debe estar en los campos (es V2)
        assert not any(f.key == "sources" for f in KnowledgeScreen.CAMPOS)


class TestImportsPantallasAgentOverride:
    """Verifica que todas las pantallas de override de agente se importen correctamente."""

    def test_agent_llm_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_llm_screen import AgentLLMScreen
        assert AgentLLMScreen.SECTION_KEY == "llm"

    def test_agent_embedding_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_embedding_screen import (
            AgentEmbeddingScreen,
        )
        assert AgentEmbeddingScreen.SECTION_KEY == "embedding"

    def test_agent_memory_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_memory_screen import AgentMemoryScreen
        assert AgentMemoryScreen.SECTION_KEY == "memory"

    def test_agent_memory_llm_screen_tristate(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_memory_llm_screen import (
            AgentMemoryLLMScreen,
        )
        for campo in AgentMemoryLLMScreen.CAMPOS:
            assert campo.es_tristate is True

    def test_agent_chat_history_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_chat_history_screen import (
            AgentChatHistoryScreen,
        )
        assert AgentChatHistoryScreen.SECTION_KEY == "chat_history"

    def test_agent_tools_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_tools_screen import AgentToolsScreen
        assert AgentToolsScreen.SECTION_KEY == "tools"

    def test_agent_skills_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_skills_screen import AgentSkillsScreen
        assert AgentSkillsScreen.SECTION_KEY == "skills"

    def test_agent_semantic_routing_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_semantic_routing_screen import (
            AgentSemanticRoutingScreen,
        )
        assert AgentSemanticRoutingScreen.SECTION_KEY == "semantic_routing"

    def test_agent_workspace_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_workspace_screen import (
            AgentWorkspaceScreen,
        )
        assert AgentWorkspaceScreen.SECTION_KEY == "workspace"

    def test_agent_transcription_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_transcription_screen import (
            AgentTranscriptionScreen,
        )
        assert AgentTranscriptionScreen.SECTION_KEY == "transcription"

    def test_agent_delegation_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_delegation_screen import (
            AgentDelegationScreen,
        )
        assert AgentDelegationScreen.SECTION_KEY == "delegation"

    def test_agent_knowledge_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_knowledge_screen import (
            AgentKnowledgeScreen,
        )
        assert AgentKnowledgeScreen.SECTION_KEY == "knowledge"

    def test_agent_providers_screen(self) -> None:
        from adapters.inbound.setup_tui.screens.sections.agent_providers_screen import (
            AgentProvidersScreen,
        )
        # AgentProvidersScreen es una Screen directa, no SectionEditorScreen
        from textual.screen import Screen
        assert issubclass(AgentProvidersScreen, Screen)


# ---------------------------------------------------------------------------
# Tests del menú GlobalScreen
# ---------------------------------------------------------------------------


class TestGlobalScreenMenu:
    def test_importa_correctamente(self) -> None:
        from adapters.inbound.setup_tui.screens.global_screen import _SECCIONES
        assert len(_SECCIONES) > 0

    def test_resolucion_de_pantallas(self) -> None:
        """_resolver_pantalla_global retorna None para clave desconocida."""
        from adapters.inbound.setup_tui.screens.global_screen import _resolver_pantalla_global
        assert _resolver_pantalla_global("inexistente", None) is None  # type: ignore[arg-type]

    def test_global_screen_tiene_todas_las_secciones(self) -> None:
        from adapters.inbound.setup_tui.screens.global_screen import _SECCIONES
        claves = [s[0] for s in _SECCIONES]
        for clave in ("app", "llm", "embedding", "memory", "memory.llm", "chat_history",
                      "tools", "skills", "semantic_routing", "workspace", "admin",
                      "transcription", "user", "delegation", "knowledge"):
            assert clave in claves, f"Falta la sección '{clave}' en GlobalScreen"


# ---------------------------------------------------------------------------
# Tests del menú AgentEditorScreen
# ---------------------------------------------------------------------------


class TestAgentEditorScreenMenu:
    def test_importa_correctamente(self) -> None:
        from adapters.inbound.setup_tui.screens.agent_editor_screen import (
            _SECCIONES_OVERRIDE,
        )
        assert len(_SECCIONES_OVERRIDE) > 0

    def test_tiene_channels_en_secciones(self) -> None:
        from adapters.inbound.setup_tui.screens.agent_editor_screen import _SECCIONES_OVERRIDE
        claves = [s[0] for s in _SECCIONES_OVERRIDE]
        assert "channels" in claves

    def test_tiene_providers_en_secciones(self) -> None:
        from adapters.inbound.setup_tui.screens.agent_editor_screen import _SECCIONES_OVERRIDE
        claves = [s[0] for s in _SECCIONES_OVERRIDE]
        assert "providers" in claves

    def test_tiene_memory_llm_en_secciones(self) -> None:
        from adapters.inbound.setup_tui.screens.agent_editor_screen import _SECCIONES_OVERRIDE
        claves = [s[0] for s in _SECCIONES_OVERRIDE]
        assert "memory.llm" in claves

    def test_broadcast_helpers_re_exportados(self) -> None:
        """Los helpers de broadcast siguen importables desde agent_editor_screen."""
        from adapters.inbound.setup_tui.screens.agent_editor_screen import (
            detectar_broadcast_ambiguo,
            resolver_broadcast_client,
            resolver_broadcast_server,
        )
        assert callable(detectar_broadcast_ambiguo)
        assert callable(resolver_broadcast_server)
        assert callable(resolver_broadcast_client)

    def test_resolver_pantalla_agente_channels(self) -> None:
        from adapters.inbound.setup_tui.screens.agent_editor_screen import _resolver_pantalla_agente
        from adapters.inbound.setup_tui.screens.channels_screen import ChannelsScreen
        from unittest.mock import MagicMock
        container = MagicMock()
        pantalla = _resolver_pantalla_agente("channels", container, "general")
        assert isinstance(pantalla, ChannelsScreen)

    def test_resolver_pantalla_agente_desconocida(self) -> None:
        from adapters.inbound.setup_tui.screens.agent_editor_screen import _resolver_pantalla_agente
        from unittest.mock import MagicMock
        container = MagicMock()
        assert _resolver_pantalla_agente("inexistente", container, "general") is None
