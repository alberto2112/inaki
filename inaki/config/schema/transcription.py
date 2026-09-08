"""Bloque ``transcription``: transcripción de voz.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from inaki.config.schema._base import _ConfigBaseModel


class TranscriptionConfig(_ConfigBaseModel):
    """Config del provider de transcripción de audio (opcional)."""

    provider: str = "groq"
    """KEY del registry ``providers:`` que transcribe (endpoint Whisper OpenAI-compat).

    El bloque entero es opcional, pero si un canal tiene ``voice_enabled: true``
    y no hay ``transcription:`` (ni en el agente ni en el global), el arranque
    del agente falla con un error explícito en vez de ignorar los audios."""

    model: str = "whisper-large-v3-turbo"
    """Modelo de transcripción, en el nombre que espera el provider."""

    language: str | None = None
    """Idioma esperado del audio en ISO-639-1 (``"es"``, ``"en"``). ``null`` → autodetect.

    Es solo el default: el canal puede pasar un idioma por llamada y ese gana.
    Fijarlo mejora la precisión cuando se sabe que el audio siempre viene en un
    idioma; una cadena vacía no se manda al provider."""

    timeout_seconds: int = 60
    """Timeout HTTP del request de transcripción, en segundos."""

    max_audio_mb: int = 25
    """Tamaño máximo del audio en MB. Un fichero mayor se rechaza ANTES de subirlo.

    El límite se chequea localmente y levanta ``TranscriptionFileTooLargeError``
    sin gastar red ni cuota. El default ``25`` es el techo del endpoint de Groq
    Whisper — subirlo por encima de lo que acepta el provider solo cambia dónde
    falla."""
