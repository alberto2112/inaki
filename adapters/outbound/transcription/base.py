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

        - `files["file"] = (filename, bytes, mime)` — `audio` es nombre genérico;
          Whisper ignora el nombre y usa el mime para detectar el formato.
        - `data["model"]` siempre presente.
        - `data["language"]` sólo si es un string no vacío (evita mandar '' al provider).
        """
        files: dict[str, Any] = {"file": ("audio", audio, mime)}
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
