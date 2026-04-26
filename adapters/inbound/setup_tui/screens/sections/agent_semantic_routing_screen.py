"""Pantalla de override de ``semantic_routing`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentSemanticRoutingScreen(SectionEditorScreen):
    """Override de la sección ``semantic_routing`` en la capa del agente."""

    SECTION_KEY = "semantic_routing"
    TITULO = "Semantic Routing — Override de agente"
    CAMPOS = (
        FieldSpec(
            "min_words_threshold",
            int,
            "Override del umbral de palabras mínimas",
            placeholder="",
        ),
    )
