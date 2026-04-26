"""Pantalla de override de ``skills`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentSkillsScreen(SectionEditorScreen):
    """Override de la sección ``skills`` en la capa del agente."""

    SECTION_KEY = "skills"
    TITULO = "Skills — Override de agente"
    CAMPOS = (
        FieldSpec(
            "semantic_routing_top_k",
            int,
            "Override del top-K de skills",
            placeholder="",
        ),
        FieldSpec(
            "semantic_routing_min_score",
            float,
            "Override del score mínimo",
            placeholder="",
        ),
        FieldSpec(
            "sticky_ttl",
            int,
            "Override del TTL sticky de skills",
            placeholder="",
        ),
    )
