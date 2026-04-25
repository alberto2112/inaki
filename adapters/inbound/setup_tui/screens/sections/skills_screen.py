"""Pantalla de edición de la sección ``skills``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class SkillsScreen(SectionEditorScreen):
    """Edita la sección ``skills`` de ``global.yaml``."""

    SECTION_KEY = "skills"
    TITULO = "Skills — Selección de habilidades"
    CAMPOS = (
        FieldSpec(
            "semantic_routing_min_skills",
            int,
            "Mínimo de skills antes de activar semantic routing",
            placeholder="10",
        ),
        FieldSpec(
            "semantic_routing_top_k",
            int,
            "Top-K skills seleccionadas por turno",
            placeholder="3",
        ),
        FieldSpec(
            "semantic_routing_min_score",
            float,
            "Score mínimo para incluir una skill",
            placeholder="0.0",
        ),
        FieldSpec(
            "sticky_ttl",
            int,
            "Turnos que una skill seleccionada sobrevive (0 = desactivado)",
            placeholder="3",
        ),
    )
