"""Pantalla de edición de la sección ``workspace``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class WorkspaceScreen(SectionEditorScreen):
    """Edita la sección ``workspace`` de ``global.yaml``."""

    SECTION_KEY = "workspace"
    TITULO = "Workspace — Directorio de trabajo del agente"
    CAMPOS = (
        FieldSpec(
            "path",
            str,
            "Directorio raíz (~ expandido automáticamente)",
            placeholder="~/inaki-workspace",
        ),
        FieldSpec(
            "containment",
            str,
            "Modo de contención",
            enum_choices=("strict", "warn", "off"),
            placeholder="strict",
        ),
    )
