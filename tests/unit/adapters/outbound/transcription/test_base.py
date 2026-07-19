"""Tests de BaseTranscriptionProvider.

Contrato:
- Hereda de ITranscriptionProvider (ABC).
- Es concreta para la familia OpenAI-compatible: implementa `transcribe` una
  sola vez; los providers concretos solo declaran `_DEFAULT_BASE_URL` y
  `_PROVIDER_LABEL`.
- Valida credenciales cuando `REQUIRES_CREDENTIALS` está activo.
- Provee helpers compartidos: `_format_response_log` y `_build_multipart`.
"""

from __future__ import annotations

import pytest

from adapters.outbound.transcription.base import (
    BaseTranscriptionProvider,
    ResolvedTranscriptionConfig,
)
from core.domain.errors import TranscriptionError
from core.ports.outbound.transcription_port import ITranscriptionProvider


def test_base_hereda_de_port() -> None:
    assert issubclass(BaseTranscriptionProvider, ITranscriptionProvider)


def test_valida_api_key_cuando_requiere_credenciales() -> None:
    # La base es concreta (OpenAI-compatible): instanciable, pero exige creds.
    cfg = ResolvedTranscriptionConfig(provider="openai", model="whisper-1", api_key=None)
    with pytest.raises(TranscriptionError) as exc_info:
        BaseTranscriptionProvider(cfg)
    assert "api_key" in str(exc_info.value).lower()


def test_format_response_log_incluye_provider_y_length() -> None:
    log = BaseTranscriptionProvider._format_response_log("Groq", "hola mundo")
    assert "Groq" in log
    assert "len=10" in log
    assert "hola mundo" in log


def test_format_response_log_trunca_preview_a_200_chars() -> None:
    texto = "a" * 500
    log = BaseTranscriptionProvider._format_response_log("Groq", texto)
    # El preview no debe contener los 500 chars.
    assert "len=500" in log
    # Sólo 200 chars del contenido deben aparecer.
    assert log.count("a") <= 220  # margen por texto del log mismo


def test_build_multipart_sin_language() -> None:
    files, data = BaseTranscriptionProvider._build_multipart(
        audio=b"\x00\x01",
        mime="audio/ogg",
        model="whisper-large-v3-turbo",
    )
    # El filename lleva extensión derivada del mime para que Whisper detecte el formato.
    assert files["file"] == ("audio.ogg", b"\x00\x01", "audio/ogg")
    assert data == {"model": "whisper-large-v3-turbo"}
    assert "language" not in data


def test_build_multipart_extension_derivada_del_mime() -> None:
    """El filename incluye la extensión correcta para cada MIME conocido."""
    casos = [
        ("audio/ogg", ".ogg"),
        ("audio/mpeg", ".mp3"),
        ("audio/wav", ".wav"),
        ("video/mp4", ".mp4"),
        ("audio/x-desconocido", ""),  # mime desconocido → sin extensión
    ]
    for mime, ext_esperada in casos:
        files, _ = BaseTranscriptionProvider._build_multipart(audio=b"x", mime=mime, model="m")
        filename = files["file"][0]
        assert filename == f"audio{ext_esperada}", (
            f"mime={mime!r}: esperaba 'audio{ext_esperada}', got {filename!r}"
        )


def test_build_multipart_con_language() -> None:
    files, data = BaseTranscriptionProvider._build_multipart(
        audio=b"xx",
        mime="audio/mpeg",
        model="whisper-large-v3-turbo",
        language="es",
    )
    assert data["model"] == "whisper-large-v3-turbo"
    assert data["language"] == "es"


def test_build_multipart_language_vacio_no_se_incluye() -> None:
    """Language = '' o None no deben colarse al payload."""
    _, data = BaseTranscriptionProvider._build_multipart(
        audio=b"x",
        mime="audio/ogg",
        model="m",
        language="",
    )
    assert "language" not in data
