"""Pantalla de override de ``memory`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentMemoryScreen(SectionEditorScreen):
    """Override de la sección ``memory`` en la capa del agente."""

    SECTION_KEY = "memory"
    TITULO = "Memory — Override de agente"
    CAMPOS = (
        FieldSpec(
            "enabled",
            bool,
            "Activar memoria para este agente",
            placeholder="true",
        ),
        FieldSpec(
            "default_top_k",
            int,
            "Override del top-K de recuerdos por turno",
            placeholder="",
        ),
        FieldSpec(
            "min_relevance_score",
            float,
            "Override del umbral mínimo de relevancia",
            placeholder="",
        ),
    )
