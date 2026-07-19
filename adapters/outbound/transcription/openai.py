"""Proveedor de transcripción via OpenAI Whisper API.

Toda la lógica vive en ``BaseTranscriptionProvider`` (dialecto OpenAI
``/audio/transcriptions`` — el original). Acá solo se declaran los ClassVars
propios de OpenAI. Se auto-descubre via ``PROVIDER_NAME`` (mismo patrón que Groq).
"""

from __future__ import annotations

from typing import ClassVar

from adapters.outbound.transcription.base import BaseTranscriptionProvider

PROVIDER_NAME = "openai"


class OpenAITranscriptionProvider(BaseTranscriptionProvider):
    _DEFAULT_BASE_URL: ClassVar[str] = "https://api.openai.com/v1"
    _PROVIDER_LABEL: ClassVar[str] = "OpenAI"
