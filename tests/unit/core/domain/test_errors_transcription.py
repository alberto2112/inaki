"""Tests de los errores de transcripción (tasks 1.1, 1.8).

Fija la jerarquía y los atributos:
- TranscriptionError es subclase de IñakiError.
- TranscriptionFileTooLargeError lleva size_bytes y limit_bytes.
- UnknownTranscriptionProviderError es subclase de TranscriptionError.
"""

from __future__ import annotations

import pytest

from core.domain.errors import (
    IñakiError,
    TranscriptionError,
    TranscriptionFileTooLargeError,
    UnknownTranscriptionProviderError,
)


def test_transcription_error_hereda_de_iñaki_error() -> None:
    err = TranscriptionError("boom")
    assert isinstance(err, IñakiError)
    assert str(err) == "boom"


def test_file_too_large_hereda_de_transcription_error_y_guarda_tamaños() -> None:
    err = TranscriptionFileTooLargeError(size_bytes=30_000_000, limit_bytes=25_000_000)

    assert isinstance(err, TranscriptionError)
    assert err.size_bytes == 30_000_000
    assert err.limit_bytes == 25_000_000
    # El mensaje debe mencionar ambos tamaños para que sea útil en logs/Telegram.
    assert "30000000" in str(err) or "30_000_000" in str(err) or "30" in str(err)
    assert "25000000" in str(err) or "25_000_000" in str(err) or "25" in str(err)


def test_unknown_provider_error_hereda_de_transcription_error() -> None:
    err = UnknownTranscriptionProviderError("provider 'foo' no registrado")

    assert isinstance(err, TranscriptionError)
    assert isinstance(err, IñakiError)
    assert "foo" in str(err)


def test_file_too_large_se_puede_lanzar_y_capturar_como_transcription_error() -> None:
    with pytest.raises(TranscriptionError) as exc_info:
        raise TranscriptionFileTooLargeError(size_bytes=1, limit_bytes=0)
    assert isinstance(exc_info.value, TranscriptionFileTooLargeError)
