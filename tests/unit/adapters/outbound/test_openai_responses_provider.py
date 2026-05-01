"""Tests para OpenAIResponsesProvider — adapter de la Responses API.

Cobertura:
- Input mapping (USER, ASSISTANT, ASSISTANT+tool_calls, TOOL).
- Tool schema conversion (chat-completions → responses, forma plana).
- Output parsing (text blocks, function_calls).
- Errores HTTP envueltos en LLMError.
- reasoning_effort y temperature en el payload.
- stream() lanza LLMError (no implementado intencionalmente).
"""

from __future__ import annotations

import json

import httpx
import pytest

from adapters.outbound.providers.openai_responses import OpenAIResponsesProvider
from core.domain.entities.message import Message, Role
from core.domain.errors import LLMError
from infrastructure.config import ResolvedLLMConfig


def _cfg(**overrides) -> ResolvedLLMConfig:
    base = dict(
        provider="openai_responses",
        model="gpt-5.3-codex",
        temperature=0.2,
        max_tokens=2048,
        api_key="sk-test",
    )
    base.update(overrides)
    return ResolvedLLMConfig(**base)


# ---------------------------------------------------------------------------
# Input mapping
# ---------------------------------------------------------------------------


def test_build_input_user_message() -> None:
    messages = [Message(role=Role.USER, content="hola")]
    result = OpenAIResponsesProvider._build_input(messages)
    assert result == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hola"}],
        }
    ]


def test_build_input_assistant_text_only() -> None:
    messages = [Message(role=Role.ASSISTANT, content="respuesta")]
    result = OpenAIResponsesProvider._build_input(messages)
    assert result == [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "respuesta"}],
        }
    ]


def test_build_input_assistant_with_tool_calls_no_text() -> None:
    """ASSISTANT sin texto pero con tool_calls → solo emite function_call items."""
    tc = [
        {
            "id": "call_abc",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q":"test"}'},
        }
    ]
    messages = [Message(role=Role.ASSISTANT, content="", tool_calls=tc)]

    result = OpenAIResponsesProvider._build_input(messages)

    assert result == [
        {
            "type": "function_call",
            "call_id": "call_abc",
            "name": "search",
            "arguments": '{"q":"test"}',
        }
    ]


def test_build_input_assistant_with_tool_calls_and_text() -> None:
    """ASSISTANT con texto Y tool_calls → emite message + N function_call."""
    tc = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "tool_a", "arguments": "{}"},
        },
        {
            "id": "call_2",
            "type": "function",
            "function": {"name": "tool_b", "arguments": '{"x":1}'},
        },
    ]
    messages = [Message(role=Role.ASSISTANT, content="voy a buscar", tool_calls=tc)]

    result = OpenAIResponsesProvider._build_input(messages)

    assert len(result) == 3
    assert result[0]["type"] == "message"
    assert result[0]["content"] == [{"type": "output_text", "text": "voy a buscar"}]
    assert result[1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "tool_a",
        "arguments": "{}",
    }
    assert result[2] == {
        "type": "function_call",
        "call_id": "call_2",
        "name": "tool_b",
        "arguments": '{"x":1}',
    }


def test_build_input_tool_message() -> None:
    messages = [
        Message(role=Role.TOOL, content='{"result": 42}', tool_call_id="call_xyz")
    ]
    result = OpenAIResponsesProvider._build_input(messages)
    assert result == [
        {
            "type": "function_call_output",
            "call_id": "call_xyz",
            "output": '{"result": 42}',
        }
    ]


def test_build_input_full_conversation() -> None:
    """Round-trip multi-turno: user → assistant+tool → tool result → assistant final."""
    tc = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "f", "arguments": "{}"},
        }
    ]
    messages = [
        Message(role=Role.USER, content="haceme algo"),
        Message(role=Role.ASSISTANT, content="", tool_calls=tc),
        Message(role=Role.TOOL, content="resultado", tool_call_id="call_1"),
        Message(role=Role.ASSISTANT, content="listo"),
    ]

    result = OpenAIResponsesProvider._build_input(messages)

    types = [item["type"] for item in result]
    assert types == ["message", "function_call", "function_call_output", "message"]


# ---------------------------------------------------------------------------
# Tool schema conversion
# ---------------------------------------------------------------------------


def test_convert_tools_flattens_function_block() -> None:
    """chat-completions (nested) → responses (flat)."""
    tools_chat = [
        {
            "type": "function",
            "function": {
                "name": "delegate",
                "description": "Delega tareas a sub-agentes",
                "parameters": {
                    "type": "object",
                    "properties": {"agent_id": {"type": "string"}},
                },
            },
        }
    ]

    converted = OpenAIResponsesProvider._convert_tools(tools_chat)

    assert converted == [
        {
            "type": "function",
            "name": "delegate",
            "description": "Delega tareas a sub-agentes",
            "parameters": {
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
            },
        }
    ]


def test_convert_tools_passes_through_non_function_types() -> None:
    """Items que no son type=function se conservan tal cual (futuro-proof)."""
    tools = [{"type": "web_search_preview"}]
    assert OpenAIResponsesProvider._convert_tools(tools) == tools


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def test_parse_output_text_only_message() -> None:
    output = [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "respuesta final"}],
        }
    ]
    text_blocks, tool_calls = OpenAIResponsesProvider._parse_output(output)
    assert text_blocks == ["respuesta final"]
    assert tool_calls == []


def test_parse_output_function_call_remapped_to_chat_format() -> None:
    """function_call → formato chat-completions que espera el tool loop."""
    output = [
        {
            "type": "function_call",
            "id": "fc_internal",
            "call_id": "call_external",
            "name": "search",
            "arguments": '{"q":"X"}',
        }
    ]
    text_blocks, tool_calls = OpenAIResponsesProvider._parse_output(output)

    assert text_blocks == []
    assert tool_calls == [
        {
            "id": "call_external",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q":"X"}'},
        }
    ]


def test_parse_output_text_and_function_calls_mixed() -> None:
    output = [
        {
            "type": "message",
            "content": [{"type": "output_text", "text": "voy a buscar"}],
        },
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "search",
            "arguments": "{}",
        },
    ]
    text_blocks, tool_calls = OpenAIResponsesProvider._parse_output(output)

    assert text_blocks == ["voy a buscar"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "search"


def test_parse_output_ignores_unknown_item_types() -> None:
    """Items tipo reasoning, web_search_call, etc. se descartan silenciosamente."""
    output = [
        {"type": "reasoning", "summary": "..."},
        {"type": "web_search_call", "id": "w1"},
        {
            "type": "message",
            "content": [{"type": "output_text", "text": "ok"}],
        },
    ]
    text_blocks, tool_calls = OpenAIResponsesProvider._parse_output(output)
    assert text_blocks == ["ok"]
    assert tool_calls == []


# ---------------------------------------------------------------------------
# complete() — integración con httpx mockeado
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_response_payload() -> dict:
    return {
        "id": "resp_123",
        "model": "gpt-5.3-codex",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hecho"}],
            }
        ],
    }


async def test_complete_posts_to_responses_endpoint(monkeypatch, fake_response_payload):
    """El POST va a `/v1/responses` con el payload correcto."""
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self): ...
        def json(self):
            return fake_response_payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    provider = OpenAIResponsesProvider(_cfg())
    messages = [Message(role=Role.USER, content="hola")]
    tools = [
        {
            "type": "function",
            "function": {"name": "f", "description": "d", "parameters": {}},
        }
    ]

    result = await provider.complete(messages, "system prompt", tools=tools)

    assert captured["url"].endswith("/responses")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["json"]["model"] == "gpt-5.3-codex"
    assert captured["json"]["instructions"] == "system prompt"
    assert captured["json"]["temperature"] == 0.2
    assert captured["json"]["max_output_tokens"] == 2048
    # tools convertidas a forma plana
    assert captured["json"]["tools"] == [
        {"type": "function", "name": "f", "description": "d", "parameters": {}}
    ]
    assert captured["json"]["tool_choice"] == "auto"
    assert result.text_blocks == ["hecho"]


async def test_complete_includes_reasoning_effort_and_omits_temperature(
    monkeypatch, fake_response_payload
):
    """Reasoning models (codex/o1/o3) no aceptan temperature → se omite si reasoning_effort está seteado."""
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self): ...
        def json(self):
            return fake_response_payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, *, headers, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    # temperature=0.2 explícito en el config, pero como reasoning_effort está seteado,
    # debe NO viajar en el payload (la API rechaza temperature en esos modelos).
    provider = OpenAIResponsesProvider(_cfg(temperature=0.2, reasoning_effort="high"))
    await provider.complete([Message(role=Role.USER, content="hi")], "sys")

    assert captured["json"]["reasoning"] == {"effort": "high"}
    assert "temperature" not in captured["json"], (
        "temperature no debe enviarse cuando reasoning_effort está seteado"
    )


async def test_complete_omits_tools_when_none(monkeypatch, fake_response_payload):
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self): ...
        def json(self):
            return fake_response_payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, *, headers, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    provider = OpenAIResponsesProvider(_cfg())
    await provider.complete([Message(role=Role.USER, content="hi")], "sys")

    assert "tools" not in captured["json"]
    assert "tool_choice" not in captured["json"]


async def test_complete_wraps_http_error_as_llm_error(monkeypatch):
    class FakeResponse:
        status_code = 404
        text = '{"error": {"message": "model not found"}}'

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "404", request=httpx.Request("POST", "x"), response=self  # type: ignore[arg-type]
            )

        def json(self):
            return {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    provider = OpenAIResponsesProvider(_cfg())
    with pytest.raises(LLMError) as exc_info:
        await provider.complete([Message(role=Role.USER, content="hi")], "sys")

    assert "404" in str(exc_info.value)
    assert "model not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Validación de creds y stream()
# ---------------------------------------------------------------------------


def test_init_raises_without_api_key() -> None:
    with pytest.raises(LLMError, match="api_key"):
        OpenAIResponsesProvider(_cfg(api_key=None))


async def test_stream_raises_not_implemented() -> None:
    provider = OpenAIResponsesProvider(_cfg())
    with pytest.raises(LLMError, match="no está implementado"):
        async for _ in provider.stream(
            [Message(role=Role.USER, content="x")], "sys"
        ):
            pass


# ---------------------------------------------------------------------------
# Auto-discovery por LLMProviderFactory
# ---------------------------------------------------------------------------


def test_provider_name_and_factory_discovery() -> None:
    """El factory debe descubrir 'openai_responses' como provider disponible."""
    from infrastructure.factories.llm_factory import LLMProviderFactory

    LLMProviderFactory._registry.clear()  # forzar reload
    LLMProviderFactory._load()

    assert "openai_responses" in LLMProviderFactory._registry
    assert (
        LLMProviderFactory._registry["openai_responses"].__name__
        == "OpenAIResponsesProvider"
    )
