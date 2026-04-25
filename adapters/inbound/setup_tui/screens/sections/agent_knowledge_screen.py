"""Pantalla de override de ``knowledge`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentKnowledgeScreen(SectionEditorScreen):
    """Override de los flags top-level de ``knowledge`` en la capa del agente."""

    SECTION_KEY = "knowledge"
    TITULO = "Knowledge — Override de agente"
    CAMPOS = (
        FieldSpec(
            "enabled",
            bool,
            "Override de activación del pipeline de knowledge",
            placeholder="",
        ),
        FieldSpec(
            "include_memory",
            bool,
            "Override de inclusión de memoria SQLite como fuente",
            placeholder="",
        ),
        FieldSpec(
            "top_k_per_source",
            int,
            "Override del top-K global por fuente",
            placeholder="",
        ),
        FieldSpec(
            "min_score",
            float,
            "Override del score mínimo global",
            placeholder="",
        ),
        FieldSpec(
            "max_total_chunks",
            int,
            "Override del límite duro de chunks totales",
            placeholder="",
        ),
    )
