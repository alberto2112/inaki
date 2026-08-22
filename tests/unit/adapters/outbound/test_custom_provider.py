"""Tests para CustomProvider — endpoint propio con dialecto OpenAI clásico.

Cobertura de lo que DISTINGUE a este adapter de ``openai`` (si no, no valdría la
pena que exista):
- ``base_url`` obligatorio → ``ConfigError`` que nombra el campo.
- Construye sin ``api_key`` (``REQUIRES_CREDENTIALS = False``).
- Sin key → NO manda ``Authorization`` (nada de ``Bearer None``).
- Con key → sí lo manda (hay servers propios con auth).
- Payload con ``max_tokens`` (dialecto clásico), nunca ``max_completion_tokens``.
- Autodiscovery: la factory lo registra bajo la key ``custom``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from adapters.outbound.providers.base import ResolvedLLMConfig
from adapters.outbound.providers.custom import CustomProvider
from core.domain.entities.message import Message, Role
from core.domain.errors import ConfigError
from infrastructure.factories.llm_factory import LLMProviderFactory

_BASE_URL = "http://192.168.1.50:8000/v1"


def _cfg(**overrides) -> ResolvedLLMConfig:
    base: dict[str, Any] = dict(
        provider="custom",
        model="unsloth/qwen3-8b",
        temperature=0.7,
        max_tokens=2048,
        base_url=_BASE_URL,
    )
    base.update(overrides)
    return ResolvedLLMConfig(**base)


# ---------------------------------------------------------------------------
# base_url obligatorio
# ---------------------------------------------------------------------------


def test_sin_base_url_falla_con_config_error_que_nombra_el_campo() -> None:
    """Un adapter que se define por su endpoint no puede adivinarlo. Fail-fast
    con el path exacto del YAML, no un connection-refused contra localhost."""
    with pytest.raises(ConfigError) as exc:
        CustomProvider(_cfg(base_url=None))

    assert "providers.custom.base_url" in str(exc.value)


def test_base_url_de_config_arma_el_endpoint() -> None:
    provider = CustomProvider(_cfg())

    assert provider._chat_url == f"{_BASE_URL}/chat/completions"


# ---------------------------------------------------------------------------
# Credenciales opcionales
# ---------------------------------------------------------------------------


def test_construye_sin_api_key() -> None:
    """El caso normal de un server propio: entra pelado, sin secreto inventado.

    ``OpenAICompatibleProvider.__init__`` levanta ``LLMError`` si
    ``REQUIRES_CREDENTIALS`` y no hay key — este test fija que custom lo apaga.
    """
    provider = CustomProvider(_cfg())

    assert provider._cfg.api_key is None


def test_sin_api_key_no_manda_authorization() -> None:
    """El footgun: la base manda ``Bearer {api_key}`` siempre, y sin key eso
    viaja como el literal ``"Bearer None"``. Un server que valida el header
    responde 401 sin explicar nada."""
    provider = CustomProvider(_cfg())

    assert "Authorization" not in provider._headers
    assert provider._headers["Content-Type"] == "application/json"


def test_con_api_key_manda_authorization() -> None:
    """Algunos servers propios sí piden token. Si está, se manda."""
    provider = CustomProvider(_cfg(api_key="secreto-local"))

    assert provider._headers["Authorization"] == "Bearer secreto-local"


# ---------------------------------------------------------------------------
# Dialecto clásico
# ---------------------------------------------------------------------------


def test_usa_max_tokens_y_no_max_completion_tokens() -> None:
    """La diferencia con ``openai``. Un server que no entiende la clave moderna
    o tira 400, o la ignora en silencio y genera hasta agotar el contexto."""
    provider = CustomProvider(_cfg(max_tokens=1234))

    params = provider._completion_params(stream=False)

    assert params["max_tokens"] == 1234
    assert "max_completion_tokens" not in params
    assert params["temperature"] == 0.7


def test_payload_no_filtra_reasoning_effort() -> None:
    """``reasoning_effort`` es de providers cloud; un server propio no lo entiende
    y un campo inesperado puede hacerle rechazar el request."""
    provider = CustomProvider(_cfg(reasoning_effort="high"))

    payload = provider._build_payload([Message(role=Role.USER, content="hola")], "sys")

    assert "reasoning_effort" not in payload
    assert payload["max_tokens"] == 2048


# ---------------------------------------------------------------------------
# complete() sobre httpx mockeado
# ---------------------------------------------------------------------------


async def test_complete_postea_a_chat_completions_sin_authorization(monkeypatch) -> None:
    """End-to-end del adapter: URL, ausencia del header y clave de tokens."""
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None: ...

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "buenas", "tool_calls": []}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None: ...

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args) -> None: ...

        async def post(self, url, *, headers, json) -> FakeResponse:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    provider = CustomProvider(_cfg())
    result = await provider.complete([Message(role=Role.USER, content="hola")], "sys")

    assert captured["url"] == f"{_BASE_URL}/chat/completions"
    assert "Authorization" not in captured["headers"]
    assert captured["json"]["max_tokens"] == 2048
    assert result.text_blocks == ["buenas"]


# ---------------------------------------------------------------------------
# Autodiscovery
# ---------------------------------------------------------------------------


def test_factory_descubre_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PROVIDER_NAME = "custom"`` + clase concreta en el módulo → registro
    automático, sin tocar wiring."""
    monkeypatch.setattr(LLMProviderFactory, "_registry", {})
    LLMProviderFactory._load()

    assert LLMProviderFactory._registry["custom"] is CustomProvider


def test_factory_crea_custom_sin_api_key_en_el_registry() -> None:
    """``REQUIRES_CREDENTIALS = False`` → la factory no exige ``api_key``.

    Mismo trato que ``ollama``. Sin esto, arrancar contra un server sin auth te
    obliga a declarar una credencial fantasma solo para pasar el check.
    """
    from infrastructure.config import LLMConfig, ProviderConfig

    provider = LLMProviderFactory.create(
        LLMConfig(provider="custom", model="unsloth/qwen3-8b", max_tokens=2048),
        providers={"custom": ProviderConfig(base_url=_BASE_URL)},
    )

    assert isinstance(provider, CustomProvider)
