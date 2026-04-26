"""Pantalla de edición de la sección ``tools``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class ToolsScreen(SectionEditorScreen):
    """Edita la sección ``tools`` de ``global.yaml``."""

    SECTION_KEY = "tools"
    TITULO = "Tools — Selección de herramientas"
    CAMPOS = (
        FieldSpec(
            "semantic_routing_min_tools",
            int,
            "Mínimo de tools antes de activar semantic routing",
            placeholder="10",
        ),
        FieldSpec(
            "semantic_routing_top_k",
            int,
            "Top-K tools seleccionadas por turno",
            placeholder="5",
        ),
        FieldSpec(
            "semantic_routing_min_score",
            float,
            "Score mínimo para incluir una tool",
            placeholder="0.0",
        ),
        FieldSpec(
            "sticky_ttl",
            int,
            "Turnos que una tool seleccionada sobrevive (0 = desactivado)",
            placeholder="3",
        ),
        FieldSpec(
            "tool_call_max_iterations",
            int,
            "Máximo de iteraciones del tool loop por turno",
            placeholder="5",
        ),
        FieldSpec(
            "circuit_breaker_threshold",
            int,
            "Umbral de fallos repetidos para circuit breaker",
            placeholder="2",
        ),
    )
