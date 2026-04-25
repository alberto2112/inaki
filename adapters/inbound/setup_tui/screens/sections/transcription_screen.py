"""Pantalla de edición de la sección ``transcription``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class TranscriptionScreen(SectionEditorScreen):
    """Edita la sección ``transcription`` de ``global.yaml``."""

    SECTION_KEY = "transcription"
    TITULO = "Transcription — Transcripción de audio"
    CAMPOS = (
        FieldSpec(
            "provider",
            str,
            "KEY del registry de providers",
            dropdown_source="providers",
            placeholder="groq",
        ),
        FieldSpec(
            "model",
            str,
            "Modelo de transcripción",
            placeholder="whisper-large-v3-turbo",
        ),
        FieldSpec(
            "language",
            str,
            "Código ISO-639-1 (vacío = autodetectar)",
            placeholder="es",
        ),
        FieldSpec(
            "timeout_seconds",
            int,
            "Timeout en segundos para el request HTTP",
            placeholder="60",
        ),
        FieldSpec(
            "max_audio_mb",
            int,
            "Límite de tamaño de audio en MB",
            placeholder="25",
        ),
    )
