"""Tests de OpenAISceneDescriberAdapter (task 2.11 - RED).

Usa `respx` para interceptar POST a la OpenAI Chat Completions API sin red real.
Cobertura:
- PROVIDER_NAME = "openai" expuesto a nivel módulo.
- Constructor almacena configuración sin hacer HTTP calls.
- describe_image construye payload OpenAI chat completions correcto con image_url base64.
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

from adapters.outbound.scene.openai_describer import (
    PROVIDER_NAME,
    OpenAISceneDescriberAdapter,
)
from core.domain.errors import SceneDescriptionError

ENDPOINT = "https://api.openai.com/v1/chat/completions"
FAKE_IMAGE = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
FAKE_B64 = base64.b64encode(FAKE_IMAGE).decode()
FAKE_KEY = "sk-openai-test-key"
FAKE_MODEL = "gpt-4o"


def _adapter(**kwargs) -> OpenAISceneDescriberAdapter:
    defaults = {"api_key": FAKE_KEY, "model": FAKE_MODEL}
    defaults.update(kwargs)
    return OpenAISceneDescriberAdapter(**defaults)


def _fake_response(text: str = "Una plaza con árboles y personas.") -> dict:
    return {
        "id": "chatcmpl-123",
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
    assert PROVIDER_NAME == "openai"


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
# Happy path — payload y headers correctos
# ---------------------------------------------------------------------------


@respx.mock
async def test_describe_image_envia_payload_correcto() -> None:
    """Valida estructura body: model, messages con content text + image_url base64."""
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter()

    result = await adapter.describe_image(FAKE_IMAGE, prompt="¿Qué hay en la imagen?")

    assert result == "Una plaza con árboles y personas."
    assert route.called

    req = route.calls.last.request
    body = json.loads(req.content)

    assert body["model"] == FAKE_MODEL
    assert body["max_completion_tokens"] == 1024
    messages = body["messages"]
    assert len(messages) == 2

    # messages[0]: system con el prompt
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "¿Qué hay en la imagen?"

    # messages[1]: user con la imagen
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
    texto_esperado = "Un mercado concurrido con puestos de verduras."
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
        return_value=httpx.Response(401, json={"error": {"message": "Unauthorized"}})
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
