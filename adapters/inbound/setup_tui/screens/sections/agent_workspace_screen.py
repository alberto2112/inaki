"""Pantalla de override de ``workspace`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentWorkspaceScreen(SectionEditorScreen):
    """Override de la sección ``workspace`` en la capa del agente."""

    SECTION_KEY = "workspace"
    TITULO = "Workspace — Override de agente"
    CAMPOS = (
        FieldSpec(
            "path",
            str,
            "Override del directorio de trabajo del agente",
            placeholder="",
        ),
        FieldSpec(
            "containment",
            str,
            "Override del modo de contención",
            enum_choices=("strict", "warn", "off"),
            placeholder="",
        ),
    )
