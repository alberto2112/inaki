"""BaseTranscriptionProvider — lógica compartida de la familia OpenAI-compatible.

Espeja el rol de `OpenAICompatibleProvider` en los LLM: los servicios de
transcripción realistas hoy (OpenAI Whisper y Groq, que clonó su API) hablan el
MISMO dialecto — ``POST {base_url}/audio/transcriptions`` multipart con
``response_format=text``. Por eso el `transcribe` vive UNA sola vez acá y cada
provider concreto se reduce a declarar sus dos ClassVars:

- ``PROVIDER_NAME`` (a nivel módulo, para el auto-discovery de la factory).
- ``_DEFAULT_BASE_URL`` — endpoint por default del servicio.
- ``_PROVIDER_LABEL`` — etiqueta para logs y mensajes de error.

Si algún día aparece un servicio que NO sea OpenAI-compatible (otro wire format),
recién ahí se introduce una clase intermedia — no antes.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel

from core.domain.errors import TranscriptionError, TranscriptionFileTooLargeError
from core.ports.outbound.transcription_port import ITranscriptionProvider

logger = logging.getLogger(__name__)


class ResolvedTranscriptionConfig(BaseModel):
    """TranscriptionConfig + credenciales resueltas del registry.

    Vive en adapters: es el contrato de entrada que los providers declaran en
    SU capa. La factory de infrastructure lo compone desde la config YAML.
    """

    provider: str
    model: str
    language: str | None = None
    timeout_seconds: int = 60
    max_audio_mb: int = 25
    api_key: str | None = None
    base_url: str | None = None


# Groq (y OpenAI Whisper) usan la extensión del filename para detectar el formato
# cuando el content-type multipart no es suficiente.
_MIME_EXT: dict[str, str] = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".mp4",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


class BaseTranscriptionProvider(ITranscriptionProvider):
    """Base concreta para proveedores de transcripción OpenAI-compatible.

    ``REQUIRES_CREDENTIALS`` indica si la factory debe exigir una entrada en
    ``providers:`` al resolver las creds. Las subclases solo overridean los
    ClassVars ``_DEFAULT_BASE_URL`` y ``_PROVIDER_LABEL``.
    """

    REQUIRES_CREDENTIALS: bool = True
    _DEFAULT_BASE_URL: ClassVar[str] = ""
    _PROVIDER_LABEL: ClassVar[str] = "Transcription"

    def __init__(self, cfg: ResolvedTranscriptionConfig) -> None:
        if self.REQUIRES_CREDENTIALS and not cfg.api_key:
            raise TranscriptionError(
                f"{self._PROVIDER_LABEL} transcription requiere api_key en "
                f"providers.{cfg.provider}.api_key"
            )
        self._cfg = cfg
        self._base_url = cfg.base_url or self._DEFAULT_BASE_URL
        self._headers = {"Authorization": f"Bearer {cfg.api_key}"}

    @staticmethod
    def _format_response_log(provider: str, text: str) -> str:
        """Log INFO unificado por cada respuesta de transcripción."""
        preview = text[:200]
        return f"{provider} transcription: len={len(text)} preview={preview!r}"

    @staticmethod
    def _build_multipart(
        audio: bytes,
        mime: str,
        model: str,
        language: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Construye (files, data) para un POST multipart a endpoints estilo OpenAI.

        - `files["file"] = (filename, bytes, mime)` — el filename incluye la extensión
          derivada del mime para que Whisper detecte el formato correctamente.
        - `data["model"]` siempre presente.
        - `data["language"]` sólo si es un string no vacío (evita mandar '' al provider).
        """
        ext = _MIME_EXT.get(mime, "")
        files: dict[str, Any] = {"file": (f"audio{ext}", audio, mime)}
        data: dict[str, Any] = {"model": model}
        if language:
            data["language"] = language
        return files, data

    async def transcribe(
        self,
        audio: bytes,
        mime: str,
        language: str | None = None,
    ) -> str:
        limit_bytes = self._cfg.max_audio_mb * 1024 * 1024
        if len(audio) > limit_bytes:
            raise TranscriptionFileTooLargeError(size_bytes=len(audio), limit_bytes=limit_bytes)

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
        except httpx.HTTPStatusError as exc:
            body = exc.response.text
            raise TranscriptionError(
                f"{self._PROVIDER_LABEL} {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"{self._PROVIDER_LABEL} HTTP error: {exc}") from exc

        text = resp.text
        if not text or not text.strip():
            raise TranscriptionError(f"{self._PROVIDER_LABEL} devolvió una transcripción vacía")

        logger.info("%s", self._format_response_log(self._PROVIDER_LABEL, text))
        return text
