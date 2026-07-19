"""Tests de OpenAITranscriptionProvider.

Espeja los de Groq: el adapter hereda TODO el comportamiento de la base
OpenAI-compatible, así que acá se validan solo las diferencias propias
(PROVIDER_NAME, endpoint default de OpenAI, etiqueta en errores) más un
happy-path para confirmar que el template method de la base opera igual.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from adapters.outbound.transcription.base import ResolvedTranscriptionConfig
from adapters.outbound.transcription.openai import (
    PROVIDER_NAME,
    OpenAITranscriptionProvider,
)
from core.domain.errors import TranscriptionError

DEFAULT_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"


def _cfg(**kwargs) -> ResolvedTranscriptionConfig:
    base: dict = {
        "provider": "openai",
        "model": "whisper-1",
        "api_key": "sk-test",
    }
    base.update(kwargs)
    return ResolvedTranscriptionConfig(**base)


def test_provider_name_expuesto() -> None:
    assert PROVIDER_NAME == "openai"


def test_init_requiere_api_key() -> None:
    cfg = ResolvedTranscriptionConfig(provider="openai", model="whisper-1", api_key=None)
    with pytest.raises(TranscriptionError) as exc_info:
        OpenAITranscriptionProvider(cfg)
    assert "api_key" in str(exc_info.value).lower()


@respx.mock
async def test_transcribe_pega_al_endpoint_default_de_openai() -> None:
    route = respx.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(200, text="hola mundo"))
    provider = OpenAITranscriptionProvider(_cfg())

    result = await provider.transcribe(b"audio-bytes", "audio/ogg")

    assert result == "hola mundo"
    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer sk-test"
    body = req.content.decode("utf-8", errors="ignore")
    assert "whisper-1" in body
    assert 'name="response_format"' in body


@respx.mock
async def test_error_menciona_openai_no_groq() -> None:
    respx.post(DEFAULT_ENDPOINT).mock(return_value=httpx.Response(400, text="bad audio"))
    provider = OpenAITranscriptionProvider(_cfg())

    with pytest.raises(TranscriptionError) as exc_info:
        await provider.transcribe(b"x", "audio/ogg")
    assert "OpenAI" in str(exc_info.value)


@respx.mock
async def test_base_url_custom_sobreescribe_default() -> None:
    custom = "https://mi-proxy.internal/v1/audio/transcriptions"
    route = respx.post(custom).mock(return_value=httpx.Response(200, text="ok"))
    provider = OpenAITranscriptionProvider(_cfg(base_url="https://mi-proxy.internal/v1"))

    result = await provider.transcribe(b"x", "audio/ogg")

    assert result == "ok"
    assert route.called
