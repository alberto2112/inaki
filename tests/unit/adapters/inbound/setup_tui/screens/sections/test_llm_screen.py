"""
Tests de integración de LLMScreen.

Verifica campos, tipos y que la pantalla es subclase de SectionEditorScreen.
No monta el widget en headless — probamos la estructura declarativa.
"""

from __future__ import annotations


from adapters.inbound.setup_tui.screens.sections._base import SectionEditorScreen
from adapters.inbound.setup_tui.screens.sections.llm_screen import LLMScreen


class TestLLMScreen:
    def test_es_subclase_de_section_editor(self) -> None:
        assert issubclass(LLMScreen, SectionEditorScreen)

    def test_section_key_correcto(self) -> None:
        assert LLMScreen.SECTION_KEY == "llm"

    def test_titulo_no_vacio(self) -> None:
        assert LLMScreen.TITULO != ""

    def test_campos_incluyen_provider_y_model(self) -> None:
        claves = [f.key for f in LLMScreen.CAMPOS]
        assert "provider" in claves
        assert "model" in claves

    def test_provider_tiene_dropdown_source(self) -> None:
        provider = next(f for f in LLMScreen.CAMPOS if f.key == "provider")
        assert provider.dropdown_source == "providers"

    def test_temperature_es_float(self) -> None:
        temp = next(f for f in LLMScreen.CAMPOS if f.key == "temperature")
        assert temp.tipo is float

    def test_max_tokens_es_int(self) -> None:
        mt = next(f for f in LLMScreen.CAMPOS if f.key == "max_tokens")
        assert mt.tipo is int

    def test_ninguno_tiene_tristate(self) -> None:
        for campo in LLMScreen.CAMPOS:
            assert campo.es_tristate is False
