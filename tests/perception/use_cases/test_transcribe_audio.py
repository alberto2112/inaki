"""``TranscribeAudioUseCase``: límite, idioma, provider y transcripción vacía — fuera del canal."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from inaki.perception.settings import TranscriptionSettings
from inaki.perception.use_cases.transcribe_audio import (
    EmptyTranscriptionError,
    TranscribeAudioUseCase,
)
from inaki.shared.errors import TranscriptionError, TranscriptionFileTooLargeError


def _uc(texto: str | None = "hola", *, max_mb: int = 1, language: str | None = "es"):
    provider = AsyncMock()
    provider.transcribe = AsyncMock(return_value=texto)
    return TranscribeAudioUseCase(
        provider, TranscriptionSettings(language=language, max_audio_mb=max_mb)
    ), provider


async def test_transcribe_pasa_idioma_y_devuelve_texto() -> None:
    uc, provider = _uc("hola mundo")

    assert await uc.execute(b"audio", "audio/ogg") == "hola mundo"
    provider.transcribe.assert_awaited_once_with(b"audio", "audio/ogg", language="es")


async def test_tamano_declarado_por_encima_del_limite_no_llama_al_provider() -> None:
    uc, provider = _uc(max_mb=1)

    with pytest.raises(TranscriptionFileTooLargeError) as exc:
        await uc.execute(b"x", "audio/ogg", declared_size=2 * 1024 * 1024)

    assert exc.value.limit_bytes == 1024 * 1024
    provider.transcribe.assert_not_awaited()


def test_check_size_permite_rechazar_antes_de_descargar() -> None:
    uc, _ = _uc(max_mb=1)

    uc.check_size(1024 * 1024)  # justo en el límite: pasa
    with pytest.raises(TranscriptionFileTooLargeError):
        uc.check_size(1024 * 1024 + 1)


async def test_sin_tamano_declarado_usa_el_real() -> None:
    uc, provider = _uc(max_mb=1)

    with pytest.raises(TranscriptionFileTooLargeError):
        await uc.execute(b"x" * (1024 * 1024 + 1), "audio/ogg")
    provider.transcribe.assert_not_awaited()


@pytest.mark.parametrize("vacio", ["", "   ", None])
async def test_transcripcion_vacia_es_error_propio(vacio) -> None:
    uc, _ = _uc(vacio)

    with pytest.raises(EmptyTranscriptionError):
        await uc.execute(b"audio", "audio/ogg")


async def test_error_del_provider_propaga_como_transcription_error() -> None:
    uc, provider = _uc()
    provider.transcribe.side_effect = TranscriptionError("formato no soportado")

    with pytest.raises(TranscriptionError, match="formato"):
        await uc.execute(b"audio", "audio/ogg")
