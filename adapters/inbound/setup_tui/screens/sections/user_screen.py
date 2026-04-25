"""Pantalla de edición de la sección ``user``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class UserScreen(SectionEditorScreen):
    """Edita la sección ``user`` de ``global.yaml``."""

    SECTION_KEY = "user"
    TITULO = "User — Preferencias del usuario"
    CAMPOS = (
        FieldSpec(
            "timezone",
            str,
            "Zona horaria IANA (vacío = autodetectar desde el host)",
            placeholder="America/Argentina/Buenos_Aires",
        ),
    )
