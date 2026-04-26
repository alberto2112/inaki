"""Pantalla de override de ``llm`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentLLMScreen(SectionEditorScreen):
    """Override de la sección ``llm`` en la capa del agente."""

    SECTION_KEY = "llm"
    TITULO = "LLM — Override de agente"
    CAMPOS = (
        FieldSpec(
            "provider",
            str,
            "Override del provider (vacío = heredar del global)",
            dropdown_source="providers",
            placeholder="",
        ),
        FieldSpec(
            "model",
            str,
            "Override del modelo",
            placeholder="",
        ),
        FieldSpec(
            "temperature",
            float,
            "Override de la temperatura",
            placeholder="",
        ),
        FieldSpec(
            "max_tokens",
            int,
            "Override del máximo de tokens",
            placeholder="",
        ),
        FieldSpec(
            "reasoning_effort",
            str,
            "Override del esfuerzo de razonamiento",
            placeholder="",
        ),
    )
