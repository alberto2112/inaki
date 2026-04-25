"""Pantalla de edición de la sección ``admin``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AdminScreen(SectionEditorScreen):
    """Edita la sección ``admin`` de ``global.yaml``."""

    SECTION_KEY = "admin"
    TITULO = "Admin — Servidor de administración del daemon"
    CAMPOS = (
        FieldSpec(
            "host",
            str,
            "Host donde escucha el admin server",
            placeholder="127.0.0.1",
        ),
        FieldSpec(
            "port",
            int,
            "Puerto del admin server",
            placeholder="6497",
        ),
        FieldSpec(
            "chat_timeout",
            float,
            "Timeout en segundos para turnos de chat vía REST",
            placeholder="300.0",
        ),
    )
