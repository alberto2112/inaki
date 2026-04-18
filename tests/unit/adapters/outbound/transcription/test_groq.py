"""Tests de GroqTranscriptionProvider (task 2.5).

Usa `respx` para interceptar el POST a la Groq API sin red real.
Cobertura:
- PROVIDER_NAME.
- __init__ valida api_key obligatoria.
- POST al endpoint correcto con Authorization Bearer y multipart.
- Response parse → retorna `text`.
- HTTP 4xx/5xx → TranscriptionError.
- Response vacía / sin campo `text` → TranscriptionError.
- `base_url` custom sobreescribe el default de Groq.
- `language` del config se usa si el caller no provee uno.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from adapters.outbound.transcription.groq import (
    PROVIDER_NAME,
    GroqTranscriptionProvider,
)
from core.domain.errors import TranscriptionError, TranscriptionFileTooLargeError
from infrastructure.config import TranscriptionConfig

DEFAULT_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"


def _cfg(**kwargs) -> TranscriptionConfig:
    base: dict = {
        "provider": "groq",
        "model": "whisper-large-v3-turbo",
        "api_key": "sk-test",
    }
    base.update(kwargs)
    return TranscriptionConfig(**base)


def test_provider_name_expuesto() -> None:
    assert PROVIDER_NAME == "groq"


def test_init_requiere_api_key() -> None:
    cfg = TranscriptionConfig(provider="groq", model="whisper-large-v3-turbo")
    with pytest.raises(TranscriptionError) as exc_info:
        GroqTranscriptionProvider(cfg)
    assert "api_key" in str(exc_info.value).lower()


@respx.mock
async def test_transcribe_happy_path() -> None:
    # Con response_format=text, Groq devuelve texto plano (no JSON).
    route = respx.post(DEFAULT_ENDPOINT).mock(
        return_value=httpx.Response(200, text="hola mundo")
    )
    provider = GroqTranscriptionProvider(_cfg())

    result = await provider.transcribe(b"audio-bytes", "audio/ogg")

    assert result == "hola mundo"
    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer sk-test"
    # multipart incluye 'model', el archivo y response_format="text".
    body = req.content.decode("utf-8", errors="ignore")
    assert "whisper-large-v3-turbo" in body
    assert "audio-bytes" in body
    assert "name=\"response_format\"" in body
    assert "\r\n\r\ntext\r\n" in body or "\ntext\n" in body


@respx.mock
async def test_transcribe_con_language_explicito_tiene_prioridad() -> None:
    respx.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(200, text="ok"))
    provider = GroqTranscriptionProvider(_cfg(language="en"))

    await provider.transcribe(b"x", "audio/ogg", language="es")

    body = respx.calls.last.request.content.decode("utf-8", errors="ignore")
    assert "name=\"language\"" in body
    # El lang explícito del caller (es) gana sobre el default del cfg (en).
    assert "\r\n\r\nes\r\n" in body or "\nes\n" in body


@respx.mock
async def test_transcribe_usa_language_del_cfg_si_caller_no_da() -> None:
    respx.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(200, text="ok"))
    provider = GroqTranscriptionProvider(_cfg(language="es"))

    await provider.transcribe(b"x", "audio/ogg")

    body = respx.calls.last.request.content.decode("utf-8", errors="ignore")
    assert "name=\"language\"" in body
    assert "\r\n\r\nes\r\n" in body or "\nes\n" in body


@respx.mock
async def test_transcribe_sin_language_en_ningun_lado_no_manda_campo() -> None:
    respx.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(200, text="ok"))
    provider = GroqTranscriptionProvider(_cfg())

    await provider.transcribe(b"x", "audio/ogg")

    body = respx.calls.last.request.content.decode("utf-8", errors="ignore")
    assert "name=\"language\"" not in body


@respx.mock
async def test_transcribe_4xx_lanza_transcription_error() -> None:
    respx.post(DEFAULT_ENDPOINT).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad audio"}})
    )
    provider = GroqTranscriptionProvider(_cfg())

    with pytest.raises(TranscriptionError):
        await provider.transcribe(b"x", "audio/ogg")


@respx.mock
async def test_transcribe_5xx_lanza_transcription_error() -> None:
    respx.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(503))
    provider = GroqTranscriptionProvider(_cfg())

    with pytest.raises(TranscriptionError):
        await provider.transcribe(b"x", "audio/ogg")


@respx.mock
async def test_transcribe_texto_vacio_lanza_transcription_error() -> None:
    respx.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(200, text=""))
    provider = GroqTranscriptionProvider(_cfg())

    with pytest.raises(TranscriptionError) as exc_info:
        await provider.transcribe(b"x", "audio/ogg")
    assert "vac" in str(exc_info.value).lower()


@respx.mock
async def test_transcribe_texto_solo_whitespace_lanza_transcription_error() -> None:
    respx.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(200, text="   \n  "))
    provider = GroqTranscriptionProvider(_cfg())

    with pytest.raises(TranscriptionError):
        await provider.transcribe(b"x", "audio/ogg")


@respx.mock
async def test_base_url_custom_sobreescribe_default() -> None:
    custom_endpoint = "https://mi-proxy.internal/openai/v1/audio/transcriptions"
    route = respx.post(custom_endpoint).mock(return_value=httpx.Response(200, text="ok"))
    provider = GroqTranscriptionProvider(
        _cfg(base_url="https://mi-proxy.internal/openai/v1")
    )

    result = await provider.transcribe(b"x", "audio/ogg")

    assert result == "ok"
    assert route.called


# ---------------------------------------------------------------------------
# Size-check pre-envío (spec R2: MUST validar antes del request)
# ---------------------------------------------------------------------------


@respx.mock
async def test_audio_mayor_al_limite_lanza_file_too_large_sin_hacer_request() -> None:
    """Si `len(audio) > max_audio_mb * 1024 * 1024` el adapter NO debe pegar al endpoint."""
    route = respx.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(200, text="unused"))
    audio = b"x" * (1 * 1024 * 1024 + 1)  # 1 MB + 1 byte
    provider = GroqTranscriptionProvider(_cfg(max_audio_mb=1))

    with pytest.raises(TranscriptionFileTooLargeError) as exc_info:
        await provider.transcribe(audio, "audio/ogg")

    assert exc_info.value.size_bytes == len(audio)
    assert exc_info.value.limit_bytes == 1 * 1024 * 1024
    assert not route.called


async def test_audio_mayor_al_limite_reporta_ambos_bytes() -> None:
    audio = b"x" * (2 * 1024 * 1024 + 7)  # 2 MB + 7 bytes, límite 1 MB
    provider = GroqTranscriptionProvider(_cfg(max_audio_mb=1))

    with pytest.raises(TranscriptionFileTooLargeError) as exc_info:
        await provider.transcribe(audio, "audio/ogg")

    assert exc_info.value.size_bytes == 2 * 1024 * 1024 + 7
    assert exc_info.value.limit_bytes == 1 * 1024 * 1024


async def test_audio_en_el_limite_exacto_pasa_el_guard() -> None:
    """audio == max_audio_mb * 1024 * 1024 bytes cae dentro del límite (guard es estricto >)."""
    import respx as _respx

    audio = b"x" * (1 * 1024 * 1024)  # exactamente 1 MB
    provider = GroqTranscriptionProvider(_cfg(max_audio_mb=1))

    with _respx.mock() as router:
        router.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(200, text="ok"))
        result = await provider.transcribe(audio, "audio/ogg")

    assert result == "ok"


# ---------------------------------------------------------------------------
# Timeout (spec: MUST lanzar TranscriptionError con causa TimeoutError)
# ---------------------------------------------------------------------------


@respx.mock
async def test_timeout_del_provider_lanza_transcription_error_con_causa() -> None:
    respx.post(DEFAULT_ENDPOINT).mock(side_effect=httpx.TimeoutException("slow"))
    provider = GroqTranscriptionProvider(_cfg())

    with pytest.raises(TranscriptionError) as exc_info:
        await provider.transcribe(b"x", "audio/ogg")

    # El spec pide que la causa original sea el timeout del transporte.
    assert isinstance(exc_info.value.__cause__, httpx.TimeoutException)
