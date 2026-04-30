"""Tests de GroqSceneDescriberAdapter (task 2.13 - RED).

Usa `respx` para interceptar POST al endpoint de Groq Vision sin red real.
Groq Vision usa el mismo formato OpenAI-compatible (chat completions).
Cobertura:
- PROVIDER_NAME = "groq" expuesto a nivel módulo.
- Constructor almacena configuración sin hacer HTTP calls.
- describe_image usa el endpoint Groq (api.groq.com/openai/v1/chat/completions).
- Payload OpenAI-compatible: image_url con data URL base64, text prompt.
- Headers correctos: Authorization Bearer, Content-Type.
- Parsea respuesta exitosa y retorna el texto de la escena.
- HTTP 4xx/5xx → SceneDescriptionError.
- Timeout → SceneDescriptionError.
- Prompt argument tiene prioridad sobre prompt_template sobre default.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from adapters.outbound.scene.groq_describer import (
    PROVIDER_NAME,
    GroqSceneDescriberAdapter,
)
from core.domain.errors import SceneDescriptionError

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
FAKE_IMAGE = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
FAKE_B64 = base64.b64encode(FAKE_IMAGE).decode()
FAKE_KEY = "gsk-groq-test-key"
FAKE_MODEL = "llama-3.2-90b-vision-preview"


def _adapter(**kwargs) -> GroqSceneDescriberAdapter:
    defaults = {"api_key": FAKE_KEY, "model": FAKE_MODEL}
    defaults.update(kwargs)
    return GroqSceneDescriberAdapter(**defaults)


def _fake_response(text: str = "Un estadio lleno de gente.") -> dict:
    return {
        "id": "chatcmpl-groq-123",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": "stop",
            }
        ],
    }


# ---------------------------------------------------------------------------
# PROVIDER_NAME
# ---------------------------------------------------------------------------


def test_provider_name_expuesto() -> None:
    assert PROVIDER_NAME == "groq"


# ---------------------------------------------------------------------------
# Constructor — no hace HTTP calls
# ---------------------------------------------------------------------------


def test_init_almacena_config_sin_http() -> None:
    adapter = _adapter()
    assert adapter is not None


def test_init_con_prompt_template_custom() -> None:
    adapter = _adapter(prompt_template="Describí brevemente.")
    assert adapter is not None


# ---------------------------------------------------------------------------
# Happy path — URL Groq, payload y headers correctos
# ---------------------------------------------------------------------------


@respx.mock
async def test_describe_image_usa_endpoint_groq() -> None:
    """Verifica que el POST va a la URL de Groq, no a OpenAI."""
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter()

    await adapter.describe_image(FAKE_IMAGE)

    assert route.called


@respx.mock
async def test_describe_image_envia_payload_correcto() -> None:
    """Valida estructura body OpenAI-compatible: model, messages con text + image_url."""
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter()

    result = await adapter.describe_image(FAKE_IMAGE, prompt="¿Qué ocurre aquí?")

    assert result == "Un estadio lleno de gente."
    assert route.called

    req = route.calls.last.request
    body = json.loads(req.content)

    assert body["model"] == FAKE_MODEL
    assert body["max_completion_tokens"] == 1024
    messages = body["messages"]
    assert len(messages) == 2

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "¿Qué ocurre aquí?"

    assert messages[1]["role"] == "user"
    content = messages[1]["content"]
    tipos = [bloque["type"] for bloque in content]
    assert "image_url" in tipos
    assert "text" not in tipos

    bloque_imagen = next(b for b in content if b["type"] == "image_url")
    url = bloque_imagen["image_url"]["url"]
    assert url == f"data:image/jpeg;base64,{FAKE_B64}"


@respx.mock
async def test_describe_image_headers_correctos() -> None:
    """Valida headers: Authorization Bearer y Content-Type."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response("escena"))
    )
    adapter = _adapter()

    await adapter.describe_image(FAKE_IMAGE)

    req = respx.calls.last.request
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert "application/json" in req.headers["content-type"]


@respx.mock
async def test_describe_image_retorna_texto_de_respuesta() -> None:
    texto_esperado = "Una cancha de fútbol con jugadores en movimiento."
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response(texto_esperado))
    )
    adapter = _adapter()

    result = await adapter.describe_image(FAKE_IMAGE)

    assert result == texto_esperado


# ---------------------------------------------------------------------------
# Error HTTP 4xx / 5xx → SceneDescriptionError
# ---------------------------------------------------------------------------


@respx.mock
async def test_http_401_lanza_scene_description_error() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
    )
    adapter = _adapter()

    with pytest.raises(SceneDescriptionError):
        await adapter.describe_image(FAKE_IMAGE)


@respx.mock
async def test_http_500_lanza_scene_description_error() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(500))
    adapter = _adapter()

    with pytest.raises(SceneDescriptionError):
        await adapter.describe_image(FAKE_IMAGE)


# ---------------------------------------------------------------------------
# Timeout → SceneDescriptionError
# ---------------------------------------------------------------------------


@respx.mock
async def test_timeout_lanza_scene_description_error() -> None:
    respx.post(ENDPOINT).mock(side_effect=httpx.TimeoutException("timed out"))
    adapter = _adapter()

    with pytest.raises(SceneDescriptionError):
        await adapter.describe_image(FAKE_IMAGE)


# ---------------------------------------------------------------------------
# Prioridad de prompt: argumento > prompt_template > default
# ---------------------------------------------------------------------------


@respx.mock
async def test_prompt_argumento_tiene_prioridad_sobre_template() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter(prompt_template="Template: describí.")

    await adapter.describe_image(FAKE_IMAGE, prompt="Argumento: ¿qué ves?")

    req = respx.calls.last.request
    body = json.loads(req.content)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "Argumento: ¿qué ves?"


@respx.mock
async def test_prompt_template_se_usa_si_no_hay_argumento() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter(prompt_template="Template: describí la imagen.")

    await adapter.describe_image(FAKE_IMAGE)

    req = respx.calls.last.request
    body = json.loads(req.content)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "Template: describí la imagen."


@respx.mock
async def test_prompt_default_en_espanol_cuando_no_hay_nada() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter()

    await adapter.describe_image(FAKE_IMAGE)

    req = respx.calls.last.request
    body = json.loads(req.content)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"]
    assert len(body["messages"][0]["content"]) > 10
