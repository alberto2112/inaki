"""Tests de AnthropicSceneDescriberAdapter (task 2.9 - RED).

Usa `respx` para interceptar POST a la Anthropic Messages API sin red real.
Cobertura:
- PROVIDER_NAME = "anthropic" expuesto a nivel módulo.
- Constructor almacena configuración sin hacer HTTP calls.
- describe_image construye payload Anthropic multimodal correcto (base64 image + prompt).
- Headers correctos: x-api-key, anthropic-version, content-type.
- Parsea respuesta exitosa y retorna el texto de la escena.
- HTTP 4xx/5xx → SceneDescriptionError.
- Timeout → SceneDescriptionError.
- Prompt argument tiene prioridad sobre prompt_template sobre default.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from adapters.outbound.scene.anthropic_describer import (
    PROVIDER_NAME,
    AnthropicSceneDescriberAdapter,
)
from core.domain.errors import SceneDescriptionError

ENDPOINT = "https://api.anthropic.com/v1/messages"
FAKE_IMAGE = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
FAKE_B64 = base64.b64encode(FAKE_IMAGE).decode()
FAKE_KEY = "sk-ant-test-key"
FAKE_MODEL = "claude-3-5-sonnet-20241022"


def _adapter(**kwargs) -> AnthropicSceneDescriberAdapter:
    defaults = {"api_key": FAKE_KEY, "model": FAKE_MODEL}
    defaults.update(kwargs)
    return AnthropicSceneDescriberAdapter(**defaults)


def _fake_response(text: str = "Una escena con personas sentadas.") -> dict:
    return {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": FAKE_MODEL,
        "stop_reason": "end_turn",
    }


# ---------------------------------------------------------------------------
# PROVIDER_NAME
# ---------------------------------------------------------------------------


def test_provider_name_expuesto() -> None:
    assert PROVIDER_NAME == "anthropic"


# ---------------------------------------------------------------------------
# Constructor — no hace HTTP calls
# ---------------------------------------------------------------------------


def test_init_almacena_config_sin_http() -> None:
    """El constructor NO debe disparar ningún request al instanciar."""
    adapter = _adapter()
    assert adapter is not None


def test_init_con_prompt_template_custom() -> None:
    adapter = _adapter(prompt_template="Describí esta imagen brevemente.")
    assert adapter is not None


# ---------------------------------------------------------------------------
# Happy path — payload y headers correctos
# ---------------------------------------------------------------------------


@respx.mock
async def test_describe_image_envia_payload_correcto() -> None:
    """Valida estructura del body: model, max_tokens, messages con imagen base64 y texto."""
    import json

    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter()

    result = await adapter.describe_image(FAKE_IMAGE, prompt="¿Qué ves?")

    assert result == "Una escena con personas sentadas."
    assert route.called

    req = route.calls.last.request
    body = json.loads(req.content)

    # Estructura de mensajes Anthropic multimodal
    assert body["model"] == FAKE_MODEL
    assert body["max_tokens"] == 1024

    # El prompt va en system, no en el mensaje de usuario
    assert body["system"] == "¿Qué ves?"

    messages = body["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"

    content = messages[0]["content"]
    # Solo imagen en el mensaje de usuario, sin bloque de texto
    tipos = [bloque["type"] for bloque in content]
    assert "image" in tipos
    assert "text" not in tipos

    # Bloque imagen: base64
    bloque_imagen = next(b for b in content if b["type"] == "image")
    fuente = bloque_imagen["source"]
    assert fuente["type"] == "base64"
    assert fuente["media_type"] == "image/jpeg"
    assert fuente["data"] == FAKE_B64


@respx.mock
async def test_describe_image_headers_correctos() -> None:
    """Valida headers: x-api-key, anthropic-version, content-type."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response("escena"))
    )
    adapter = _adapter()

    await adapter.describe_image(FAKE_IMAGE)

    req = respx.calls.last.request
    assert req.headers["x-api-key"] == FAKE_KEY
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert "application/json" in req.headers["content-type"]


@respx.mock
async def test_describe_image_retorna_texto_de_respuesta() -> None:
    texto_esperado = "Tres personas caminando por un parque soleado."
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


@respx.mock
async def test_mensaje_error_esta_en_espanol() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(503))
    adapter = _adapter()

    with pytest.raises(SceneDescriptionError) as exc_info:
        await adapter.describe_image(FAKE_IMAGE)

    # El mensaje debe tener contenido útil en español
    assert exc_info.value.args[0]


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
    """El prompt pasado como argumento gana sobre el prompt_template del constructor."""
    import json

    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter(prompt_template="Template: describí la imagen.")

    await adapter.describe_image(FAKE_IMAGE, prompt="Argumento: ¿qué hay acá?")

    req = respx.calls.last.request
    body = json.loads(req.content)
    assert body["system"] == "Argumento: ¿qué hay acá?"


@respx.mock
async def test_prompt_template_se_usa_si_no_hay_argumento() -> None:
    """Si no hay prompt argumento, usa el prompt_template del constructor."""
    import json

    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter(prompt_template="Template: describí la imagen.")

    await adapter.describe_image(FAKE_IMAGE)

    req = respx.calls.last.request
    body = json.loads(req.content)
    assert body["system"] == "Template: describí la imagen."


@respx.mock
async def test_prompt_default_en_espanol_cuando_no_hay_nada() -> None:
    """Sin prompt argumento ni template, usa el prompt default en español."""
    import json

    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_fake_response())
    )
    adapter = _adapter()  # sin prompt_template

    await adapter.describe_image(FAKE_IMAGE)

    req = respx.calls.last.request
    body = json.loads(req.content)
    # El prompt default debe estar en system, no vacío y en español
    assert body["system"]
    assert len(body["system"]) > 10
