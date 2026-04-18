"""BaseTranscriptionProvider — helpers compartidos para providers de transcripción.

Espeja el rol de `BaseLLMProvider` y `BaseEmbeddingProvider`:
- Hereda del port abstracto (`ITranscriptionProvider`).
- Aporta helpers estáticos reutilizables por los providers concretos
  (`_format_response_log`, `_build_multipart`).
- Deja `transcribe` abstracto para obligar al concrete provider a implementarlo.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from core.ports.outbound.transcription_port import ITranscriptionProvider

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
    """Clase base para todos los proveedores de transcripción."""

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

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        mime: str,
        language: str | None = None,
    ) -> str: ...
