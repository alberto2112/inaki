"""Pantalla de override de ``tools`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentToolsScreen(SectionEditorScreen):
    """Override de la sección ``tools`` en la capa del agente."""

    SECTION_KEY = "tools"
    TITULO = "Tools — Override de agente"
    CAMPOS = (
        FieldSpec(
            "semantic_routing_top_k",
            int,
            "Override del top-K de tools",
            placeholder="",
        ),
        FieldSpec(
            "tool_call_max_iterations",
            int,
            "Override del máximo de iteraciones del tool loop",
            placeholder="",
        ),
        FieldSpec(
            "circuit_breaker_threshold",
            int,
            "Override del umbral del circuit breaker",
            placeholder="",
        ),
        FieldSpec(
            "sticky_ttl",
            int,
            "Override del TTL sticky de tools",
            placeholder="",
        ),
    )
