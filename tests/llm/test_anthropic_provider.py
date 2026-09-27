"""Tests para AnthropicProvider — adapter nativo de la Messages API.

Cobertura:
- Input mapping dominio (OpenAI-shaped) → Anthropic (USER, ASSISTANT,
  ASSISTANT+tool_calls, tool_result, agrupamiento de tool_results consecutivos).
- Tool schema conversion (function → input_schema plano).
- Aplanado de tool_use/tool_result a texto cuando la request no lleva tools.
- Output parsing (text, tool_use re-serializado OpenAI-shaped, thinking) y
  ``provider_content`` (bloques thinking firmados para el tool loop).
- Reenvío literal del assistant en vuelo, y sin thinking si cambió system/tools.
- _build_payload: thinking adaptive, ``output_config.effort``, NUNCA
  ``temperature`` ni ``budget_tokens`` (400 en Opus 4.7+ / Sonnet 5 / Opus 5.5).
- stop_reason: refusal / max_tokens / ventana excedida → ruidosos.
- complete()/stream() con httpx mockeado; errores HTTP → LLMError.
- Validación de creds y autodiscovery por LLMProviderFactory.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from inaki.llm.anthropic import AnthropicProvider, _huella_prefijo
from inaki.llm.base import ResolvedLLMConfig
from inaki.shared.errors import ConfigError, LLMError
from inaki.shared.message import Message, Role


def _cfg(**overrides: Any) -> ResolvedLLMConfig:
    base: dict[str, Any] = dict(
        provider="anthropic",
        model="claude-opus-4-6",
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
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.USER, content="hola")], con_tools=True
    )
    assert result == [{"role": "user", "content": [{"type": "text", "text": "hola"}]}]


def test_build_messages_assistant_text_only() -> None:
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.ASSISTANT, content="respuesta")], con_tools=True
    )
    assert result[-1] == {"role": "assistant", "content": [{"type": "text", "text": "respuesta"}]}


def test_build_messages_assistant_with_tool_calls_and_text() -> None:
    tc = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        }
    ]
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.ASSISTANT, content="voy a buscar", tool_calls=tc)], con_tools=True
    )
    assert result[-1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "voy a buscar"},
            {"type": "tool_use", "id": "call_1", "name": "search", "input": {"q": "x"}},
        ],
    }


def test_build_messages_assistant_tool_calls_no_text() -> None:
    tc = [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.ASSISTANT, content="", tool_calls=tc)], con_tools=True
    )
    assert result[-1]["content"] == [{"type": "tool_use", "id": "c1", "name": "f", "input": {}}]


def test_build_messages_tool_result_becomes_user_block() -> None:
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.TOOL, content='{"result": 42}', tool_call_id="call_xyz")], con_tools=True
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
    result = AnthropicProvider._build_anthropic_messages(msgs, con_tools=True)
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
    result = AnthropicProvider._build_anthropic_messages(msgs, con_tools=True)
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
    result = AnthropicProvider._build_anthropic_messages(msgs, con_tools=True)
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
# Input mapping — casos que rompían con el API actual
# ---------------------------------------------------------------------------


def _tc(call_id: str = "c1", name: str = "f", args: str = "{}") -> list[dict]:
    return [{"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}]


def test_build_messages_never_emits_empty_text_blocks() -> None:
    """La API rechaza bloques de texto vacíos: user vacío y assistant vacío se omiten."""
    msgs = [
        Message(role=Role.USER, content="hola"),
        Message(role=Role.ASSISTANT, content="   "),
        Message(role=Role.USER, content=""),
        Message(role=Role.USER, content="sigo"),
    ]
    result = AnthropicProvider._build_anthropic_messages(msgs, con_tools=True)
    textos = [b.get("text") for m in result for b in m["content"]]
    assert "" not in textos and "   " not in textos
    assert [m["role"] for m in result] == ["user", "user"]


def test_build_messages_empty_tool_result_gets_placeholder() -> None:
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.TOOL, content="", tool_call_id="c1")], con_tools=True
    )
    assert result[0]["content"][0]["content"] == "(sin salida)"


def test_build_messages_prepends_user_when_window_starts_with_assistant() -> None:
    result = AnthropicProvider._build_anthropic_messages(
        [Message(role=Role.ASSISTANT, content="hola"), Message(role=Role.USER, content="?")],
        con_tools=True,
    )
    assert result[0]["role"] == "user"
    assert result[1] == {"role": "assistant", "content": [{"type": "text", "text": "hola"}]}


def test_build_messages_flattens_tool_blocks_without_tools() -> None:
    """Sin tools en la request (wrap-up, fallback) no puede haber tool_use/tool_result."""
    msgs = [
        Message(role=Role.USER, content="buscá"),
        Message(role=Role.ASSISTANT, content="", tool_calls=_tc("c1", "search", '{"q":"x"}')),
        Message(role=Role.TOOL, content="encontré 3", tool_call_id="c1"),
    ]
    result = AnthropicProvider._build_anthropic_messages(msgs, con_tools=False)
    tipos = {b["type"] for m in result for b in m["content"]}
    assert tipos == {"text"}
    assert "search" in result[1]["content"][0]["text"]
    assert "encontré 3" in result[2]["content"][0]["text"]


def test_build_messages_replays_provider_content_verbatim() -> None:
    """El assistant en vuelo vuelve LITERAL, con su thinking firmado y en su orden."""
    bloques = [
        {"type": "thinking", "thinking": "pienso", "signature": "firma"},
        {"type": "text", "text": "voy"},
        {"type": "tool_use", "id": "c1", "name": "f", "input": {}},
    ]
    msg = Message(
        role=Role.ASSISTANT,
        content="voy",
        tool_calls=_tc(),
        provider_content={"content": bloques, "huella": "h1"},
    )
    result = AnthropicProvider._build_anthropic_messages([msg], con_tools=True, huella="h1")
    assert result[-1] == {"role": "assistant", "content": bloques}


def test_build_messages_strips_thinking_when_prefix_changed() -> None:
    """Si cambiaron system/tools (page-in), el thinking guardado ya no es válido."""
    bloques = [
        {"type": "thinking", "thinking": "pienso", "signature": "firma"},
        {"type": "redacted_thinking", "data": "xx"},
        {"type": "tool_use", "id": "c1", "name": "f", "input": {}},
    ]
    msg = Message(
        role=Role.ASSISTANT,
        content="",
        tool_calls=_tc(),
        provider_content={"content": bloques, "huella": "vieja"},
    )
    result = AnthropicProvider._build_anthropic_messages([msg], con_tools=True, huella="nueva")
    assert result[-1]["content"] == [{"type": "tool_use", "id": "c1", "name": "f", "input": {}}]


def test_build_messages_ignores_provider_content_without_tools() -> None:
    msg = Message(
        role=Role.ASSISTANT,
        content="",
        tool_calls=_tc(),
        provider_content={"content": [{"type": "thinking", "thinking": "", "signature": "s"}]},
    )
    result = AnthropicProvider._build_anthropic_messages([msg], con_tools=False)
    assert all(b["type"] == "text" for m in result for b in m["content"])


# ---------------------------------------------------------------------------
# Output parsing — Anthropic → dominio
# ---------------------------------------------------------------------------


def test_parse_content_text_only() -> None:
    text_blocks, tool_calls, thinking, provider_content = AnthropicProvider._parse_content(
        [{"type": "text", "text": "respuesta final"}]
    )
    assert text_blocks == ["respuesta final"]
    assert tool_calls == []
    assert thinking is None
    assert provider_content is None  # sin tool_use el tool loop no lo conserva


def test_parse_content_tool_use_remapped_to_openai_shape() -> None:
    text_blocks, tool_calls, thinking, _ = AnthropicProvider._parse_content(
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
    text_blocks, _, thinking, _ = AnthropicProvider._parse_content(
        [
            {"type": "thinking", "thinking": "razoné así", "signature": "sig"},
            {"type": "text", "text": "ok"},
        ]
    )
    assert text_blocks == ["ok"]
    assert thinking == "razoné así"


def test_parse_content_provider_content_keeps_signed_thinking_and_order() -> None:
    _, _, _, provider_content = AnthropicProvider._parse_content(
        [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "redacted_thinking", "data": "opaco"},
            {"type": "text", "text": "voy", "citations": None},
            {"type": "text", "text": ""},
            {"type": "tool_use", "id": "t1", "name": "f", "input": {"a": 1}, "caller": None},
        ],
        huella="h",
    )
    assert provider_content == {
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "redacted_thinking", "data": "opaco"},
            {"type": "text", "text": "voy"},
            {"type": "tool_use", "id": "t1", "name": "f", "input": {"a": 1}},
        ],
        "huella": "h",
    }


def test_parse_content_ignores_unknown_blocks() -> None:
    text_blocks, tool_calls, _, _ = AnthropicProvider._parse_content(
        [{"type": "redacted_thinking", "data": "..."}, {"type": "text", "text": "ok"}]
    )
    assert text_blocks == ["ok"]
    assert tool_calls == []


# ---------------------------------------------------------------------------
# _build_payload — sampling, thinking y effort
# ---------------------------------------------------------------------------

_MODELOS = ["claude-opus-4-6", "claude-sonnet-5", "claude-opus-5-5"]
_TOOLS = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {}}}]


@pytest.mark.parametrize("model", _MODELOS)
@pytest.mark.parametrize("tools", [None, _TOOLS])
@pytest.mark.parametrize("effort", [None, "low", "high"])
def test_payload_never_sends_rejected_params(model, tools, effort) -> None:
    """Ni temperature ni budget_tokens ni thinking disabled: 400 en los modelos actuales."""
    provider = AnthropicProvider(_cfg(model=model, reasoning_effort=effort))
    payload = provider._build_payload([Message(role=Role.USER, content="x")], "sys", tools)
    assert "temperature" not in payload
    assert "top_p" not in payload and "top_k" not in payload
    thinking = payload.get("thinking")
    assert thinking is None or thinking["type"] == "adaptive"
    assert "budget_tokens" not in json.dumps(payload)


def test_payload_thinking_adaptive_with_effort_even_with_tools() -> None:
    provider = AnthropicProvider(_cfg(reasoning_effort="high", max_tokens=16000))
    payload = provider._build_payload([Message(role=Role.USER, content="x")], "sys", _TOOLS)
    assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert payload["output_config"] == {"effort": "high"}
    assert payload["system"] == "sys"
    assert payload["max_tokens"] == 16000
    assert "tools" in payload


def test_payload_low_effort_without_thinking() -> None:
    provider = AnthropicProvider(_cfg(reasoning_effort="low"))
    payload = provider._build_payload([Message(role=Role.USER, content="x")], "sys", None)
    assert "thinking" not in payload
    assert payload["output_config"] == {"effort": "low"}


def test_payload_no_effort_no_thinking_params() -> None:
    provider = AnthropicProvider(_cfg(reasoning_effort=None))
    payload = provider._build_payload([Message(role=Role.USER, content="x")], "sys", None)
    assert "thinking" not in payload
    assert "output_config" not in payload


def test_payload_passes_fingerprint_of_system_and_tools() -> None:
    """La huella con la que se valida el thinking guardado es la de ESTA request."""
    bloques = [
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "tool_use", "id": "c1", "name": "f", "input": {}},
    ]
    provider = AnthropicProvider(_cfg())
    convertidas = AnthropicProvider._convert_tools(_TOOLS)
    msgs = [
        Message(role=Role.USER, content="x"),
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=_tc(),
            provider_content={"content": bloques, "huella": _huella_prefijo("sys", convertidas)},
        ),
        Message(role=Role.TOOL, content="r", tool_call_id="c1"),
    ]
    mismo = provider._build_payload(msgs, "sys", _TOOLS)
    assert mismo["messages"][1]["content"][0]["type"] == "thinking"
    otro_system = provider._build_payload(msgs, "sys cambiado", _TOOLS)
    assert otro_system["messages"][1]["content"][0]["type"] == "tool_use"


@pytest.mark.parametrize("effort", ["xhigh", "max", "MEDIUM"])
def test_init_accepts_valid_efforts(effort) -> None:
    AnthropicProvider(_cfg(reasoning_effort=effort))


def test_init_rejects_unknown_effort() -> None:
    with pytest.raises(ConfigError, match="reasoning_effort"):
        AnthropicProvider(_cfg(reasoning_effort="altísimo"))


# ---------------------------------------------------------------------------
# complete() — httpx mockeado
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_payload() -> dict:
    return {
        "id": "msg_123",
        "model": "claude-opus-4-6",
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
    assert captured["json"]["model"] == "claude-opus-4-6"
    assert "temperature" not in captured["json"]
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


def _mock_post(monkeypatch, respuesta: dict) -> dict:
    """Mockea httpx para que ``post`` devuelva ``respuesta``; captura el payload."""
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self): ...
        def json(self):
            return respuesta

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, *, headers, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())
    return captured


async def test_complete_tool_loop_roundtrip_preserves_signed_thinking(monkeypatch) -> None:
    """Iteración 1 devuelve thinking+tool_use; la 2 debe reenviarlo literal."""
    respuesta = {
        "content": [
            {"type": "thinking", "thinking": "resumen", "signature": "firma-1"},
            {"type": "tool_use", "id": "toolu_1", "name": "f", "input": {"a": 1}},
        ],
        "stop_reason": "tool_use",
    }
    captured = _mock_post(monkeypatch, respuesta)
    provider = AnthropicProvider(_cfg(reasoning_effort="high", max_tokens=16000))
    user = Message(role=Role.USER, content="hacé algo")
    r1 = await provider.complete([user], "sys", tools=_TOOLS)
    assert r1.provider_content is not None

    assistant = Message(
        role=Role.ASSISTANT,
        content=r1.text,
        tool_calls=r1.tool_calls,
        thinking=r1.thinking,
        provider_content=r1.provider_content,
    )
    tool = Message(role=Role.TOOL, content="ok", tool_call_id="toolu_1")
    await provider.complete([user, assistant, tool], "sys", tools=_TOOLS)
    reenviado = captured["json"]["messages"][1]
    assert reenviado["content"][0] == {
        "type": "thinking",
        "thinking": "resumen",
        "signature": "firma-1",
    }
    assert reenviado["content"][1]["type"] == "tool_use"


async def test_complete_refusal_raises(monkeypatch) -> None:
    _mock_post(
        monkeypatch,
        {"content": [], "stop_reason": "refusal", "stop_details": {"category": "cyber"}},
    )
    with pytest.raises(LLMError, match="refusal.*cyber"):
        await AnthropicProvider(_cfg()).complete([Message(role=Role.USER, content="x")], "s")


async def test_complete_context_window_exceeded_raises(monkeypatch) -> None:
    _mock_post(monkeypatch, {"content": [], "stop_reason": "model_context_window_exceeded"})
    with pytest.raises(LLMError, match="ventana de contexto"):
        await AnthropicProvider(_cfg()).complete([Message(role=Role.USER, content="x")], "s")


async def test_complete_max_tokens_mid_tool_use_raises(monkeypatch) -> None:
    """Un tool_use cortado por max_tokens puede traer el input truncado: no se ejecuta."""
    _mock_post(
        monkeypatch,
        {
            "content": [{"type": "tool_use", "id": "t", "name": "f", "input": {}}],
            "stop_reason": "max_tokens",
        },
    )
    with pytest.raises(LLMError, match="max_tokens"):
        await AnthropicProvider(_cfg()).complete(
            [Message(role=Role.USER, content="x")], "s", tools=_TOOLS
        )


async def test_complete_max_tokens_text_only_warns_and_returns(monkeypatch, caplog) -> None:
    _mock_post(
        monkeypatch,
        {"content": [{"type": "text", "text": "texto cor"}], "stop_reason": "max_tokens"},
    )
    result = await AnthropicProvider(_cfg()).complete([Message(role=Role.USER, content="x")], "s")
    assert result.text == "texto cor"
    assert "max_tokens" in caplog.text


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


def _mock_stream(monkeypatch, lines: list[str]) -> dict:
    captured: dict = {}

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
            captured["json"] = json
            return FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeClient())
    return captured


async def test_stream_payload_has_no_temperature(monkeypatch) -> None:
    captured = _mock_stream(monkeypatch, ['data: {"type":"message_stop"}'])
    provider = AnthropicProvider(_cfg(reasoning_effort="high"))
    _ = [c async for c in provider.stream([Message(role=Role.USER, content="hi")], "sys")]
    assert captured["json"]["stream"] is True
    assert "temperature" not in captured["json"]
    assert captured["json"]["thinking"]["type"] == "adaptive"


async def test_stream_error_event_raises(monkeypatch) -> None:
    _mock_stream(
        monkeypatch,
        ['data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'],
    )
    provider = AnthropicProvider(_cfg())
    with pytest.raises(LLMError, match="overloaded_error"):
        _ = [c async for c in provider.stream([Message(role=Role.USER, content="hi")], "sys")]


async def test_stream_refusal_raises(monkeypatch) -> None:
    _mock_stream(
        monkeypatch,
        ['data: {"type":"message_delta","delta":{"stop_reason":"refusal"}}'],
    )
    provider = AnthropicProvider(_cfg())
    with pytest.raises(LLMError, match="refusal"):
        _ = [c async for c in provider.stream([Message(role=Role.USER, content="hi")], "sys")]


# ---------------------------------------------------------------------------
# Validación de creds y autodiscovery
# ---------------------------------------------------------------------------


def test_init_raises_without_api_key() -> None:
    with pytest.raises(LLMError, match="api_key"):
        AnthropicProvider(_cfg(api_key=None))


def test_provider_name_and_factory_discovery() -> None:
    from inaki.llm.wiring import LLMProviderFactory

    LLMProviderFactory._registry.clear()
    LLMProviderFactory._load()

    assert "anthropic" in LLMProviderFactory._registry
    assert LLMProviderFactory._registry["anthropic"].__name__ == "AnthropicProvider"
