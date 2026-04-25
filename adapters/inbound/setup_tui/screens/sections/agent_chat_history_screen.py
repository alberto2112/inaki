"""Pantalla de override de ``chat_history`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentChatHistoryScreen(SectionEditorScreen):
    """Override de la sección ``chat_history`` en la capa del agente."""

    SECTION_KEY = "chat_history"
    TITULO = "Chat History — Override de agente"
    CAMPOS = (
        FieldSpec(
            "max_messages",
            int,
            "Override del máximo de mensajes al LLM (0 = sin límite)",
            placeholder="",
        ),
    )
