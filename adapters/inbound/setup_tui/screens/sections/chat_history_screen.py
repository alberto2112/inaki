"""Pantalla de edición de la sección ``chat_history``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class ChatHistoryScreen(SectionEditorScreen):
    """Edita la sección ``chat_history`` de ``global.yaml``."""

    SECTION_KEY = "chat_history"
    TITULO = "Chat History — Historial de conversación"
    CAMPOS = (
        FieldSpec(
            "db_filename",
            str,
            "Archivo SQLite del historial (relativo a ~/.inaki/)",
            placeholder="data/history.db",
        ),
        FieldSpec(
            "max_messages",
            int,
            "Últimos N mensajes al LLM (0 = sin límite)",
            placeholder="21",
        ),
    )
