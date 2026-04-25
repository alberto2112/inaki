"""Pantalla de override de ``transcription`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentTranscriptionScreen(SectionEditorScreen):
    """Override de la sección ``transcription`` en la capa del agente."""

    SECTION_KEY = "transcription"
    TITULO = "Transcription — Override de agente"
    CAMPOS = (
        FieldSpec(
            "provider",
            str,
            "Override del provider de transcripción",
            dropdown_source="providers",
            placeholder="",
        ),
        FieldSpec(
            "model",
            str,
            "Override del modelo de transcripción",
            placeholder="",
        ),
        FieldSpec(
            "language",
            str,
            "Override del idioma (código ISO-639-1, vacío = autodetectar)",
            placeholder="",
        ),
        FieldSpec(
            "timeout_seconds",
            int,
            "Override del timeout en segundos",
            placeholder="",
        ),
    )
