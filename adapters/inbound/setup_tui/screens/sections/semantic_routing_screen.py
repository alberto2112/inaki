"""Pantalla de edición de la sección ``semantic_routing``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class SemanticRoutingScreen(SectionEditorScreen):
    """Edita la sección ``semantic_routing`` de ``global.yaml``."""

    SECTION_KEY = "semantic_routing"
    TITULO = "Semantic Routing — Políticas transversales"
    CAMPOS = (
        FieldSpec(
            "min_words_threshold",
            int,
            "Palabras mínimas para activar el routing (0 = siempre activo)",
            placeholder="0",
        ),
    )
