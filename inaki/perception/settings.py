"""Settings VOs de percepción: lo que los use cases consumen, mapeado por el composition root."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


class PhotosSettings(BaseModel, frozen=True):
    """Parámetros que ``ProcessPhotoUseCase`` consume.

    Aplana ``photos.faces.*`` de la config: el use case solo necesita los dos
    umbrales, no el sub-modelo completo (provider/model son del adapter de visión).
    """

    enabled: bool = True
    debug: bool = False
    enrollment_chats: str = "private"
    match_threshold: float = 0.55
    ambiguous_threshold: float = 0.40


@dataclass(frozen=True)
class TranscriptionSettings:
    """Límites de la transcripción de voz que aplica ``TranscribeAudioUseCase``."""

    language: str | None = None
    max_audio_mb: int = 25
