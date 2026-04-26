"""Pantalla de edición de la sección ``llm`` (proveedor de lenguaje)."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class LLMScreen(SectionEditorScreen):
    """Edita la sección ``llm`` de ``global.yaml``."""

    SECTION_KEY = "llm"
    TITULO = "LLM — Proveedor de lenguaje"
    CAMPOS = (
        FieldSpec(
            "provider",
            str,
            "KEY del registry de providers",
            dropdown_source="providers",
            placeholder="openrouter",
        ),
        FieldSpec(
            "model",
            str,
            "Modelo a usar (ej: anthropic/claude-3-5-haiku)",
            placeholder="anthropic/claude-3-5-haiku",
        ),
        FieldSpec(
            "temperature",
            float,
            "Creatividad del modelo (0.0–1.0)",
            placeholder="0.7",
        ),
        FieldSpec(
            "max_tokens",
            int,
            "Máximo de tokens en la respuesta",
            placeholder="2048",
        ),
        FieldSpec(
            "reasoning_effort",
            str,
            "Esfuerzo de razonamiento (vacío = sin override)",
            placeholder="",
        ),
    )
