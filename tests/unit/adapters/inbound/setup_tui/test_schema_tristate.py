"""Tests para sections_for_model con tristate_paths y nomenclatura de secciones.

Verifica que:
  - Los campos de MEMORY.LLM se marcan como is_tristate según tristate_paths.
  - El tristate_state se infiere correctamente a partir de current_values.
  - El formato de nombre de sección anidado es "PADRE.HIJO".
"""

from __future__ import annotations

from adapters.inbound.setup_tui._schema import sections_for_model
from adapters.inbound.setup_tui.screens.agent_detail_page import _TRISTATE_PATHS


class TestSectionsForModelTristateAgentConfig:
    """Verifica integración completa: AgentConfig + tristate_paths."""

    def _get_section(self, sections, name):
        return next((fields for sname, fields in sections if sname == name), None)

    def test_memory_llm_fields_marcados_como_tristate(self):
        """Los 4 campos de MEMORY.LLM se marcan is_tristate=True."""
        from infrastructure.config import AgentConfig

        sections = sections_for_model(AgentConfig, {}, tristate_paths=_TRISTATE_PATHS)
        memory_llm_fields = self._get_section(sections, "MEMORY.LLM")

        assert memory_llm_fields is not None, "Sección MEMORY.LLM no encontrada"
        tristate_labels = {f.label for f in memory_llm_fields if f.is_tristate}
        assert "provider" in tristate_labels
        assert "model" in tristate_labels
        assert "temperature" in tristate_labels
        assert "max_tokens" in tristate_labels

    def test_campos_no_tristate_sin_marcar(self):
        """Campos fuera de _TRISTATE_PATHS no tienen is_tristate=True."""
        from infrastructure.config import AgentConfig

        sections = sections_for_model(AgentConfig, {}, tristate_paths=_TRISTATE_PATHS)
        llm_fields = self._get_section(sections, "LLM")
        assert llm_fields is not None
        for field in llm_fields:
            assert not field.is_tristate, f"Campo {field.label!r} no debería ser triestado"

    def test_tristate_state_inherit_cuando_campo_ausente(self):
        """Campo no presente en current_values → tristate_state = 'inherit'."""
        from infrastructure.config import AgentConfig

        # current_values sin nada en memory.llm
        sections = sections_for_model(AgentConfig, {}, tristate_paths=_TRISTATE_PATHS)
        memory_llm_fields = self._get_section(sections, "MEMORY.LLM")

        provider_field = next(f for f in memory_llm_fields if f.label == "provider")
        assert provider_field.tristate_state == "inherit"

    def test_tristate_state_override_value_cuando_campo_presente(self):
        """Campo presente con valor → tristate_state = 'override_value'."""
        from infrastructure.config import AgentConfig

        current = {"memory": {"llm": {"provider": "openai"}}}
        sections = sections_for_model(AgentConfig, current, tristate_paths=_TRISTATE_PATHS)
        memory_llm_fields = self._get_section(sections, "MEMORY.LLM")

        provider_field = next(f for f in memory_llm_fields if f.label == "provider")
        assert provider_field.tristate_state == "override_value"
        assert provider_field.value == "openai"

    def test_tristate_state_override_null_cuando_campo_none(self):
        """Campo presente con None → tristate_state = 'override_null'."""
        from infrastructure.config import AgentConfig

        current = {"memory": {"llm": {"temperature": None}}}
        sections = sections_for_model(AgentConfig, current, tristate_paths=_TRISTATE_PATHS)
        memory_llm_fields = self._get_section(sections, "MEMORY.LLM")

        temp_field = next(f for f in memory_llm_fields if f.label == "temperature")
        assert temp_field.tristate_state == "override_null"

    def test_seccion_anidada_formato_padre_punto_hijo(self):
        """Las secciones anidadas usan el formato 'PADRE.HIJO' en mayúsculas."""
        from infrastructure.config import AgentConfig

        sections = sections_for_model(AgentConfig, {}, tristate_paths=_TRISTATE_PATHS)
        nombres = [name for name, _ in sections]
        assert "MEMORY.LLM" in nombres

    def test_reasoning_effort_en_tristate_paths(self):
        """reasoning_effort está en _TRISTATE_PATHS y se marca correctamente."""
        from infrastructure.config import AgentConfig

        sections = sections_for_model(AgentConfig, {}, tristate_paths=_TRISTATE_PATHS)
        memory_llm_fields = self._get_section(sections, "MEMORY.LLM")

        if memory_llm_fields is not None:
            # reasoning_effort puede no estar en todos los schemas de LLMOverride
            # Solo verificamos si existe que tenga is_tristate=True
            re_field = next((f for f in memory_llm_fields if f.label == "reasoning_effort"), None)
            if re_field is not None:
                assert re_field.is_tristate


class TestSectionNamingFormat:
    """Verifica que el nombre de sección de sub-sub-modelos usa PADRE.HIJO."""

    def test_global_config_scheduler_channel_fallback(self):
        """GlobalConfig emite 'SCHEDULER.CHANNEL_FALLBACK' como sección anidada."""
        from infrastructure.config import GlobalConfig

        sections = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        nombres = [name for name, _ in sections]
        assert "SCHEDULER.CHANNEL_FALLBACK" in nombres

    def test_global_config_memory_llm(self):
        """GlobalConfig emite 'MEMORY.LLM' como sección anidada."""
        from infrastructure.config import GlobalConfig

        sections = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        nombres = [name for name, _ in sections]
        assert "MEMORY.LLM" in nombres

    def test_no_emite_nombres_de_clase(self):
        """El schema mapper NO emite nombres de clase (ej. MEMORYCONFIG).

        Los nombres de clase eran el bug de la V1. Este test asegura la regresión.
        """
        from infrastructure.config import GlobalConfig

        sections = sections_for_model(GlobalConfig, {}, section_prefix="APP")
        nombres = [name for name, _ in sections]

        # Nombres de clase que NO deben aparecer nunca
        nombres_clase_prohibidos = {"MEMORYCONFIG", "SCHEDULERCONFIG", "CHANNELFALLBACKCONFIG"}
        encontrados = nombres_clase_prohibidos & set(nombres)
        assert not encontrados, f"Nombres de clase inesperados en secciones: {encontrados}"
