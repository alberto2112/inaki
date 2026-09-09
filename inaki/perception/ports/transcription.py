"""Port para providers de transcripción de audio a texto.

Espeja el patrón de ILLMProvider e IEmbeddingProvider: ABC con método
único async `transcribe(audio, mime, language)`. El port vive en el módulo
``perception`` y no importa nada fuera del kernel y ``shared``.
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
