"""Tests para AnthropicProvider — adapter nativo de la Messages API.

Cobertura:
- Input mapping dominio (OpenAI-shaped) → Anthropic (USER, ASSISTANT,
  ASSISTANT+tool_calls, tool_result, agrupamiento de tool_results consecutivos).
- Tool schema conversion (function → input_schema plano).
- Output parsing (text, tool_use re-serializado OpenAI-shaped, thinking).
- _build_payload: gating de thinking según presencia de tools, temp=1 con
  thinking, system como param top-level.
- _thinking_budget: derivación de reasoning_effort + capeo.
- complete()/stream() con httpx mockeado; errores HTTP → LLMError.
- Validación de creds y autodiscovery por LLMProviderFactory.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from adapters.outbound.providers.anthropic import AnthropicProvider
from adapters.outbound.providers.base import ResolvedLLMConfig
from core.domain.entities.message import Message, Role
from core.domain.errors import LLMError


def _cfg(**overrides: Any) -> ResolvedLLMConfig:
    base: dict[str, Any] = dict(
        provider="anthropic",
        model="claude-opus-4-8",
        temperature=0.7,
        max_tokens=2048,
        api_key="sk-ant-test",
    )
    base.update(overrides)
    return ResolvedLLMConfig(**base)


# ---------------------------------------------------------------------------
# Input mapping — dominio → Anthropic
# ---------------------------------------------------------------------------


def test_build_messages_user() -> None:
    result = AnthropicProvider._build_anthropic_messages([Message(role=Role.USER, content="hola")])
    assert result == [{"role": "user", "content": [{"type": "text", "text": "hola"}]}]


def test_build_messages_assistant_text_only() -> None:
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.ASSISTANT, content="respuesta")]
    )
    assert result == [{"role": "assistant", "content": [{"type": "text", "text": "respuesta"}]}]


def test_build_messages_assistant_with_tool_calls_and_text() -> None:
    tc = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        }
    ]
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.ASSISTANT, content="voy a buscar", tool_calls=tc)]
    )
    assert result == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "voy a buscar"},
                {"type": "tool_use", "id": "call_1", "name": "search", "input": {"q": "x"}},
            ],
        }
    ]


def test_build_messages_assistant_tool_calls_no_text() -> None:
    tc = [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.ASSISTANT, content="", tool_calls=tc)]
    )
    assert result[0]["content"] == [{"type": "tool_use", "id": "c1", "name": "f", "input": {}}]


def test_build_messages_tool_result_becomes_user_block() -> None:
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.TOOL, content='{"result": 42}', tool_call_id="call_xyz")]
    )
    assert result == [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_xyz", "content": '{"result": 42}'}
            ],
        }
    ]


def test_build_messages_consecutive_tool_results_grouped() -> None:
    """Múltiples tool_result consecutivos → UN solo mensaje user (requisito Anthropic)."""
    msgs = [
        Message(role=Role.TOOL, content="r1", tool_call_id="c1"),
        Message(role=Role.TOOL, content="r2", tool_call_id="c2"),
    ]
    result = AnthropicProvider._build_anthropic_messages(msgs)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == [
        {"type": "tool_result", "tool_use_id": "c1", "content": "r1"},
        {"type": "tool_result", "tool_use_id": "c2", "content": "r2"},
    ]


def test_build_messages_user_after_tool_result_not_grouped() -> None:
    """Un user de texto tras un tool_result NO se agrupa con él."""
    msgs = [
        Message(role=Role.TOOL, content="r1", tool_call_id="c1"),
        Message(role=Role.USER, content="seguí"),
    ]
    result = AnthropicProvider._build_anthropic_messages(msgs)
    assert len(result) == 2
    assert result[0]["content"][0]["type"] == "tool_result"
    assert result[1]["content"] == [{"type": "text", "text": "seguí"}]


def test_build_messages_full_conversation() -> None:
    tc = [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    msgs = [
        Message(role=Role.USER, content="haceme algo"),
        Message(role=Role.ASSISTANT, content="", tool_calls=tc),
        Message(role=Role.TOOL, content="resultado", tool_call_id="c1"),
        Message(role=Role.ASSISTANT, content="listo"),
    ]
    result = AnthropicProvider._build_anthropic_messages(msgs)
    roles = [m["role"] for m in result]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert result[1]["content"][0]["type"] == "tool_use"
    assert result[2]["content"][0]["type"] == "tool_result"


# ---------------------------------------------------------------------------
# Tool schema conversion
# ---------------------------------------------------------------------------


def test_convert_tools_to_input_schema() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "delegate",
                "description": "Delega a sub-agentes",
                "parameters": {"type": "object", "properties": {"agent_id": {"type": "string"}}},
            },
        }
    ]
    assert AnthropicProvider._convert_tools(tools) == [
        {
            "name": "delegate",
            "description": "Delega a sub-agentes",
            "input_schema": {"type": "object", "properties": {"agent_id": {"type": "string"}}},
        }
    ]


def test_convert_tools_missing_parameters_defaults_empty_schema() -> None:
    tools = [{"type": "function", "function": {"name": "f", "description": "d"}}]
    converted = AnthropicProvider._convert_tools(tools)
    assert converted[0]["input_schema"] == {"type": "object", "properties": {}}


def test_convert_tools_skips_non_function() -> None:
    assert AnthropicProvider._convert_tools([{"type": "web_search"}]) == []


# ---------------------------------------------------------------------------
# Output parsing — Anthropic → dominio
# ---------------------------------------------------------------------------


def test_parse_content_text_only() -> None:
    text_blocks, tool_calls, thinking = AnthropicProvider._parse_content(
        [{"type": "text", "text": "respuesta final"}]
    )
    assert text_blocks == ["respuesta final"]
    assert tool_calls == []
    assert thinking is None


def test_parse_content_tool_use_remapped_to_openai_shape() -> None:
    text_blocks, tool_calls, thinking = AnthropicProvider._parse_content(
        [{"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"q": "x"}}]
    )
    assert text_blocks == []
    assert tool_calls == [
        {
            "id": "toolu_1",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q": "x"}'},
        }
    ]
    assert thinking is None


def test_parse_content_thinking_captured() -> None:
    text_blocks, tool_calls, thinking = AnthropicProvider._parse_content(
        [
            {"type": "thinking", "thinking": "razoné así", "signature": "sig"},
            {"type": "text", "text": "ok"},
        ]
    )
    assert text_blocks == ["ok"]
    assert thinking == "razoné así"


def test_parse_content_ignores_unknown_blocks() -> None:
    text_blocks, tool_calls, thinking = AnthropicProvider._parse_content(
        [{"type": "redacted_thinking", "data": "..."}, {"type": "text", "text": "ok"}]
    )
    assert text_blocks == ["ok"]
    assert tool_calls == []


# ---------------------------------------------------------------------------
# _thinking_budget
# ---------------------------------------------------------------------------


def test_thinking_budget_capped_by_max_tokens() -> None:
    # high=8192 pero max_tokens=2048 → ceiling 1536
    assert AnthropicProvider(_cfg(reasoning_effort="high"))._thinking_budget() == 1536


def test_thinking_budget_respects_effort_when_room_available() -> None:
    assert (
        AnthropicProvider(_cfg(reasoning_effort="medium", max_tokens=8192))._thinking_budget()
        == 4096
    )
    assert (
        AnthropicProvider(_cfg(reasoning_effort="high", max_tokens=16384))._thinking_budget()
        == 8192
    )


def test_thinking_budget_floor() -> None:
    # max_tokens chico → no baja del mínimo 1024 de Anthropic
    assert (
        AnthropicProvider(_cfg(reasoning_effort="high", max_tokens=1200))._thinking_budget() == 1024
    )


# ---------------------------------------------------------------------------
# _build_payload — gating de thinking
# ---------------------------------------------------------------------------


def test_payload_thinking_enabled_without_tools() -> None:
    provider = AnthropicProvider(_cfg(reasoning_effort="high", max_tokens=8192))
    payload = provider._build_payload([Message(role=Role.USER, content="x")], "sys", tools=None)
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 7680}
    assert payload["temperature"] == 1.0
    assert payload["system"] == "sys"
    assert payload["max_tokens"] == 8192


def test_payload_thinking_disabled_when_tools_present() -> None:
    """Con tools, thinking se desactiva (no tenemos signature para el tool loop)."""
    provider = AnthropicProvider(_cfg(reasoning_effort="high"))
    tools = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {}}}]
    payload = provider._build_payload([Message(role=Role.USER, content="x")], "sys", tools=tools)
    assert "thinking" not in payload
    assert payload["temperature"] == 0.7
    assert "tools" in payload


def test_payload_thinking_disabled_when_effort_off() -> None:
    provider = AnthropicProvider(_cfg(reasoning_effort=None))
    payload = provider._build_payload([Message(role=Role.USER, content="x")], "sys", tools=None)
    assert "thinking" not in payload
    assert payload["temperature"] == 0.7


# ---------------------------------------------------------------------------
# complete() — httpx mockeado
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_payload() -> dict:
    return {
        "id": "msg_123",
        "model": "claude-opus-4-8",
        "role": "assistant",
        "content": [{"type": "text", "text": "hecho"}],
        "stop_reason": "end_turn",
    }


async def test_complete_posts_to_messages_endpoint(monkeypatch, fake_payload) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self): ...
        def json(self):
            return fake_payload

    class FakeClient:
        def __init__(self, *a, **kw):
            captured["client_kwargs"] = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    provider = AnthropicProvider(_cfg())
    tools = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {}}}]
    result = await provider.complete(
        [Message(role=Role.USER, content="hola")], "system prompt", tools=tools
    )

    assert captured["url"].endswith("/messages")
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["json"]["model"] == "claude-opus-4-8"
    assert captured["json"]["system"] == "system prompt"
    assert captured["json"]["max_tokens"] == 2048
    # parameters:{} (falsy) → input_schema por defecto (Anthropic exige schema válido)
    assert captured["json"]["tools"] == [
        {"name": "f", "description": "d", "input_schema": {"type": "object", "properties": {}}}
    ]
    assert result.text_blocks == ["hecho"]


async def test_complete_captures_thinking(monkeypatch) -> None:
    payload = {
        "content": [
            {"type": "thinking", "thinking": "pensé esto", "signature": "s"},
            {"type": "text", "text": "respuesta"},
        ]
    }

    class FakeResponse:
        def raise_for_status(self): ...
        def json(self):
            return payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    provider = AnthropicProvider(_cfg(reasoning_effort="high"))
    result = await provider.complete([Message(role=Role.USER, content="hi")], "sys")
    assert result.text == "respuesta"
    assert result.thinking == "pensé esto"


async def test_complete_wraps_http_error(monkeypatch) -> None:
    class FakeResponse:
        status_code = 400
        text = '{"error": {"message": "max_tokens required"}}'

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "400",
                request=httpx.Request("POST", "x"),
                response=self,  # type: ignore[arg-type]
            )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    provider = AnthropicProvider(_cfg())
    with pytest.raises(LLMError) as exc_info:
        await provider.complete([Message(role=Role.USER, content="hi")], "sys")
    assert "400" in str(exc_info.value)
    assert "max_tokens required" in str(exc_info.value)


async def test_complete_uses_configured_timeout(monkeypatch, fake_payload) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self): ...
        def json(self):
            return fake_payload

    class FakeClient:
        def __init__(self, *a, **kw):
            captured["timeout"] = kw.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    provider = AnthropicProvider(_cfg(timeout_seconds=180))
    await provider.complete([Message(role=Role.USER, content="hi")], "sys")
    assert captured["timeout"] == 180


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------


async def test_stream_yields_text_deltas(monkeypatch) -> None:
    lines = [
        "event: message_start",
        'data: {"type":"message_start"}',
        "event: content_block_delta",
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hola"}}',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" mundo"}}',
        'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"x"}}',
        'data: {"type":"message_stop"}',
    ]

    class FakeStreamResponse:
        def raise_for_status(self): ...

        async def aiter_lines(self):
            for line in lines:
                yield line

    class FakeStreamCtx:
        async def __aenter__(self):
            return FakeStreamResponse()

        async def __aexit__(self, *a):
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        def stream(self, method, url, *, headers, json):
            return FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    provider = AnthropicProvider(_cfg())
    chunks = [c async for c in provider.stream([Message(role=Role.USER, content="hi")], "sys")]
    assert chunks == ["Hola", " mundo"]  # thinking_delta NO se emite


# ---------------------------------------------------------------------------
# Validación de creds y autodiscovery
# ---------------------------------------------------------------------------


def test_init_raises_without_api_key() -> None:
    with pytest.raises(LLMError, match="api_key"):
        AnthropicProvider(_cfg(api_key=None))


def test_provider_name_and_factory_discovery() -> None:
    from infrastructure.factories.llm_factory import LLMProviderFactory

    LLMProviderFactory._registry.clear()
    LLMProviderFactory._load()

    assert "anthropic" in LLMProviderFactory._registry
    assert LLMProviderFactory._registry["anthropic"].__name__ == "AnthropicProvider"
