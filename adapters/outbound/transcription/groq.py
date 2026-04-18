"""Proveedor de transcripción via Groq Whisper API (compatible OpenAI).

Endpoint: POST {base_url}/audio/transcriptions
Multipart: file (bytes), model, response_format=text, [language].
Response: texto plano (por response_format=text).
"""

from __future__ import annotations

import logging

import httpx

from adapters.outbound.transcription.base import BaseTranscriptionProvider
from core.domain.errors import TranscriptionError, TranscriptionFileTooLargeError
from infrastructure.config import TranscriptionConfig

PROVIDER_NAME = "groq"

logger = logging.getLogger(__name__)


class GroqTranscriptionProvider(BaseTranscriptionProvider):

    def __init__(self, cfg: TranscriptionConfig) -> None:
        if not cfg.api_key:
            raise TranscriptionError("Groq requiere api_key en transcription.api_key")
        self._cfg = cfg
        self._base_url = cfg.base_url or "https://api.groq.com/openai/v1"
        self._headers = {
            "Authorization": f"Bearer {cfg.api_key}",
        }

    async def transcribe(
        self,
        audio: bytes,
        mime: str,
        language: str | None = None,
    ) -> str:
        limit_bytes = self._cfg.max_audio_mb * 1024 * 1024
        if len(audio) > limit_bytes:
            raise TranscriptionFileTooLargeError(
                size_bytes=len(audio), limit_bytes=limit_bytes
            )

        lang = language or self._cfg.language
        files, data = self._build_multipart(
            audio=audio,
            mime=mime,
            model=self._cfg.model,
            language=lang,
        )
        data["response_format"] = "text"

        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_seconds) as client:
                resp = await client.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers=self._headers,
                    files=files,
                    data=data,
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"Groq HTTP error: {exc}") from exc

        text = resp.text
        if not text or not text.strip():
            raise TranscriptionError("Groq devolvió una transcripción vacía")

        logger.info("%s", self._format_response_log("Groq", text))
        return text
