"""Pantalla de edición de la sección ``delegation``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class DelegationScreen(SectionEditorScreen):
    """Edita la sección ``delegation`` de ``global.yaml``."""

    SECTION_KEY = "delegation"
    TITULO = "Delegation — Delegación agente-a-agente"
    CAMPOS = (
        FieldSpec(
            "max_iterations_per_sub",
            int,
            "Máximo de iteraciones del tool-loop por llamada delegada",
            placeholder="10",
        ),
        FieldSpec(
            "timeout_seconds",
            int,
            "Presupuesto de reloj por llamada delegada (segundos)",
            placeholder="60",
        ),
    )
