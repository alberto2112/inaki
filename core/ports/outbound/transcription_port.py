"""Port para providers de transcripción de audio a texto.

Espeja el patrón de ILLMProvider e IEmbeddingProvider: ABC con método
único async `transcribe(audio, mime, language)`. El port vive en `core/` y
no importa nada de `adapters/` ni `infrastructure/`.
"""

from abc import ABC, abstractmethod


class ITranscriptionProvider(ABC):
    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        mime: str,
        language: str | None = None,
    ) -> str: ...
