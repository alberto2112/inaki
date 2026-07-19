"""Proveedor de transcripción via Groq Whisper API (compatible OpenAI).

Toda la lógica vive en ``BaseTranscriptionProvider`` (dialecto OpenAI
``/audio/transcriptions``). Acá solo se declaran los ClassVars propios de Groq.
"""

from __future__ import annotations

from typing import ClassVar

from adapters.outbound.transcription.base import BaseTranscriptionProvider

PROVIDER_NAME = "groq"


class GroqTranscriptionProvider(BaseTranscriptionProvider):
    _DEFAULT_BASE_URL: ClassVar[str] = "https://api.groq.com/openai/v1"
    _PROVIDER_LABEL: ClassVar[str] = "Groq"
