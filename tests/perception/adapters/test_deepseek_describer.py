"""Tests de DeepSeekSceneDescriberAdapter.

Usa `respx` para interceptar POST al endpoint de DeepSeek sin red real.
DeepSeek Vision usa el formato OpenAI-compatible (chat completions), con el tope
de salida en ``max_tokens`` y ``thinking`` desactivado.
Cobertura:
- PROVIDER_NAME = "deepseek" expuesto a nivel módulo.
- Constructor almacena configuración sin hacer HTTP calls.
- describe_image usa el endpoint DeepSeek (api.deepseek.com/chat/completions).
- Payload OpenAI-compatible: image_url con data URL base64, text prompt.
- Headers correctos: Authorization Bearer, Content-Type.
- Parsea respuesta exitosa y retorna el texto de la escena.
- HTTP 4xx/5xx → SceneDescriptionError.
- Timeout → SceneDescriptionError.
- Prompt argument tiene prioridad sobre prompt_template sobre default.
- El wiring construye el adapter con ``photos.scene.provider: deepseek``.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from inaki.perception.adapters.scene.deepseek_describer import (
    PROVIDER_NAME,
    DeepSeekSceneDescriberAdapter,
)
from inaki.config import PhotosConfig, ProviderConfig
from inaki.perception.wiring import _build_scene_describer
from inaki.shared.errors import SceneDescriptionError

ENDPOINT = "https://api.deepseek.com/chat/completions"
FAKE_IMAGE = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
FAKE_B64 = base64.b64encode(FAKE_IMAGE).decode()
FAKE_KEY = "sk-deepseek-test-key"
FAKE_MODEL = "deepseek-flash"


def _adapter(**kwargs) -> DeepSeekSceneDescriberAdapter:
    defaults = {"api_key": FAKE_KEY, "model": FAKE_MODEL}
    defaults.update(kwargs)
    return DeepSeekSceneDescriberAdapter(**defaults)


def _fake_response(text: str = "Un estadio lleno de gente.") -> dict:
    return {
        "id": "chatcmpl-deepseek-123",
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
    assert PROVIDER_NAME == "deepseek"


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
# Happy path — URL DeepSeek, payload y headers correctos
# ---------------------------------------------------------------------------


@respx.mock
async def test_describe_image_usa_endpoint_deepseek() -> None:
    """Verifica que el POST va a la URL de DeepSeek, no a OpenAI."""
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_fake_response()))
    adapter = _adapter()

    await adapter.describe_image(FAKE_IMAGE)

    assert route.called


@respx.mock
async def test_describe_image_envia_payload_correcto() -> None:
    """Valida estructura body OpenAI-compatible: model, messages con text + image_url."""
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_fake_response()))
    adapter = _adapter()

    result = await adapter.describe_image(FAKE_IMAGE, prompt="¿Qué ocurre aquí?")

    assert result == "Un estadio lleno de gente."
    assert route.called

    req = route.calls.last.request
    body = json.loads(req.content)

    assert body["model"] == FAKE_MODEL
    # DeepSeek: el tope va en max_tokens y el thinking se apaga explícito.
    assert body["max_tokens"] == 1024
    assert "max_completion_tokens" not in body
    assert body["thinking"] == {"type": "disabled"}
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
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_fake_response("escena")))
    adapter = _adapter()

    await adapter.describe_image(FAKE_IMAGE)

    req = respx.calls.last.request
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert "application/json" in req.headers["content-type"]


@respx.mock
async def test_describe_image_retorna_texto_de_respuesta() -> None:
    texto_esperado = "Una cancha de fútbol con jugadores en movimiento."
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_fake_response(texto_esperado)))
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
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_fake_response()))
    adapter = _adapter(prompt_template="Template: describí.")

    await adapter.describe_image(FAKE_IMAGE, prompt="Argumento: ¿qué ves?")

    req = respx.calls.last.request
    body = json.loads(req.content)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "Argumento: ¿qué ves?"


@respx.mock
async def test_prompt_template_se_usa_si_no_hay_argumento() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_fake_response()))
    adapter = _adapter(prompt_template="Template: describí la imagen.")

    await adapter.describe_image(FAKE_IMAGE)

    req = respx.calls.last.request
    body = json.loads(req.content)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "Template: describí la imagen."


@respx.mock
async def test_prompt_default_en_espanol_cuando_no_hay_nada() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_fake_response()))
    adapter = _adapter()

    await adapter.describe_image(FAKE_IMAGE)

    req = respx.calls.last.request
    body = json.loads(req.content)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"]
    assert len(body["messages"][0]["content"]) > 10


# ---------------------------------------------------------------------------
# Wiring — photos.scene.provider: deepseek
# ---------------------------------------------------------------------------


@respx.mock
async def test_wiring_construye_deepseek_con_credencial_de_providers() -> None:
    """Sin ``scene.api_key`` propia, la credencial sale de ``providers.deepseek``."""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_fake_response()))
    photos = PhotosConfig.model_validate(
        {"scene": {"provider": "deepseek", "model": "deepseek-flash"}}
    )

    adapter = _build_scene_describer(photos.scene, {"deepseek": ProviderConfig(api_key=FAKE_KEY)})

    assert isinstance(adapter, DeepSeekSceneDescriberAdapter)
    await adapter.describe_image(FAKE_IMAGE)
    req = respx.calls.last.request
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert json.loads(req.content)["model"] == "deepseek-flash"
