"""
Tests de AgentMemoryLLMScreen — el caso de tristate.

Verifica que los 4 campos declaren es_tristate=True y que la pantalla
esté bien configurada para el modo override de agente.
"""

from __future__ import annotations


from adapters.inbound.setup_tui.screens.sections._base import SectionEditorScreen
from adapters.inbound.setup_tui.screens.sections.agent_memory_llm_screen import (
    AgentMemoryLLMScreen,
)


_CAMPOS_ESPERADOS_TRISTATE = ("model", "provider", "temperature", "max_tokens")


class TestAgentMemoryLLMScreen:
    def test_es_subclase_de_section_editor(self) -> None:
        assert issubclass(AgentMemoryLLMScreen, SectionEditorScreen)

    def test_todos_los_campos_son_tristate(self) -> None:
        for campo in AgentMemoryLLMScreen.CAMPOS:
            assert campo.es_tristate is True, (
                f"Campo {campo.key!r} debería tener es_tristate=True"
            )

    def test_campos_esperados_presentes(self) -> None:
        claves = [f.key for f in AgentMemoryLLMScreen.CAMPOS]
        for esperado in _CAMPOS_ESPERADOS_TRISTATE:
            assert esperado in claves, f"Falta campo tristate: {esperado!r}"

    def test_model_es_str(self) -> None:
        model = next(f for f in AgentMemoryLLMScreen.CAMPOS if f.key == "model")
        assert model.tipo is str

    def test_temperature_es_float(self) -> None:
        temp = next(f for f in AgentMemoryLLMScreen.CAMPOS if f.key == "temperature")
        assert temp.tipo is float

    def test_max_tokens_es_int(self) -> None:
        mt = next(f for f in AgentMemoryLLMScreen.CAMPOS if f.key == "max_tokens")
        assert mt.tipo is int

    def test_provider_tiene_dropdown_source(self) -> None:
        provider = next(f for f in AgentMemoryLLMScreen.CAMPOS if f.key == "provider")
        assert provider.dropdown_source == "providers"

    def test_titulo_menciona_triestado(self) -> None:
        assert "tri" in AgentMemoryLLMScreen.TITULO.lower()
