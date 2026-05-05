"""Payload builder y captura de ``reasoning_content`` en ``DeepSeekProvider``.

Cuando ``reasoning_effort`` activa thinking mode:
  - El payload incluye ``thinking: {type: enabled}`` y ``reasoning_effort``.
  - Se OMITEN ``temperature`` y otros parámetros de sampling no soportados
    por DeepSeek en thinking mode.

Cuando no está activo:
  - Se mantiene la semántica previa: ``temperature`` + ``thinking: {type: disabled}``.

Captura: el ``reasoning_content`` del response viaja a ``LLMResponse.thinking``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.outbound.providers.deepseek import DeepSeekProvider
from core.domain.entities.message import Message, Role
from infrastructure.config import ResolvedLLMConfig


def _cfg(reasoning_effort: str | None) -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider="deepseek",
        model="deepseek-v4-pro",
        temperature=0.7,
        max_tokens=1024,
        api_key="sk-test",
        reasoning_effort=reasoning_effort,
    )


def test_payload_thinking_disabled_keeps_temperature() -> None:
    provider = DeepSeekProvider(_cfg(None))
    msgs = [Message(role=Role.USER, content="hola")]

    payload = provider._build_payload(msgs, "sys", tools=None)

    assert payload["temperature"] == 0.7
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload


def test_payload_thinking_active_omits_temperature_and_sets_effort() -> None:
    provider = DeepSeekProvider(_cfg("high"))
    msgs = [Message(role=Role.USER, content="hola")]

    payload = provider._build_payload(msgs, "sys", tools=None)

    assert "temperature" not in payload
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"


def test_payload_low_treated_as_disabled() -> None:
    """``low`` se mapea internamente a ``high`` por DeepSeek y no aporta
    granularidad real → lo tratamos como off."""
    provider = DeepSeekProvider(_cfg("low"))
    msgs = [Message(role=Role.USER, content="hola")]

    payload = provider._build_payload(msgs, "sys", tools=None)

    assert payload["temperature"] == 0.7
    assert payload["thinking"] == {"type": "disabled"}


def test_thinking_active_property_reflects_config() -> None:
    assert DeepSeekProvider(_cfg(None)).thinking_active is False
    assert DeepSeekProvider(_cfg("low")).thinking_active is False
    assert DeepSeekProvider(_cfg("high")).thinking_active is True
    assert DeepSeekProvider(_cfg("max")).thinking_active is True


@pytest.mark.asyncio
async def test_complete_captures_reasoning_content_into_thinking() -> None:
    """End-to-end de ``complete()``: ``reasoning_content`` del response → ``LLMResponse.thinking``."""
    provider = DeepSeekProvider(_cfg("high"))

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "respuesta final",
                    "reasoning_content": "razoné así y así",
                    "tool_calls": None,
                }
            }
        ]
    }
    fake_resp.raise_for_status = MagicMock()

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.post.return_value = fake_resp

    with patch("adapters.outbound.providers.deepseek.httpx.AsyncClient", return_value=fake_client):
        result = await provider.complete([Message(role=Role.USER, content="hola")], "sys")

    assert result.text == "respuesta final"
    assert result.thinking == "razoné así y así"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_complete_thinking_none_when_response_lacks_field() -> None:
    """Si la API no devuelve ``reasoning_content`` (modo no-thinking), thinking=None."""
    provider = DeepSeekProvider(_cfg(None))

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "choices": [{"message": {"content": "hola", "tool_calls": None}}]
    }
    fake_resp.raise_for_status = MagicMock()

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.post.return_value = fake_resp

    with patch("adapters.outbound.providers.deepseek.httpx.AsyncClient", return_value=fake_client):
        result = await provider.complete([Message(role=Role.USER, content="hi")], "sys")

    assert result.thinking is None


@pytest.mark.asyncio
async def test_complete_empty_string_reasoning_content_treated_as_none() -> None:
    """DeepSeek devuelve ``reasoning_content: ""`` cuando no recibió reasoning previo
    (verificado empíricamente). Lo normalizamos a None para que el tool loop
    no propague strings vacíos como si hubiera habido razonamiento."""
    provider = DeepSeekProvider(_cfg("high"))

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "choices": [
            {"message": {"content": "x", "reasoning_content": "", "tool_calls": None}}
        ]
    }
    fake_resp.raise_for_status = MagicMock()

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.post.return_value = fake_resp

    with patch("adapters.outbound.providers.deepseek.httpx.AsyncClient", return_value=fake_client):
        result = await provider.complete([Message(role=Role.USER, content="hi")], "sys")

    assert result.thinking is None
