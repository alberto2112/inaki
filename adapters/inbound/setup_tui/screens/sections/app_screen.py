"""Pantalla de edición de la sección ``app`` (configuración general del sistema)."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AppScreen(SectionEditorScreen):
    """Edita la sección ``app`` de ``global.yaml``."""

    SECTION_KEY = "app"
    TITULO = "Configuración de la aplicación"
    CAMPOS = (
        FieldSpec("name", str, "Nombre del asistente", placeholder="Iñaki"),
        FieldSpec(
            "log_level",
            str,
            "Nivel de log",
            enum_choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
            placeholder="INFO",
        ),
        FieldSpec(
            "default_agent",
            str,
            "Agente por defecto para CLI",
            dropdown_source="agents",
            placeholder="general",
        ),
    )
