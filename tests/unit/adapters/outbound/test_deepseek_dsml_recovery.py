"""Workaround DSML en ``DeepSeekProvider``.

Bug conocido del modelo: con thinking activo, DeepSeek a veces serializa las
tool calls como markup DSML dentro de ``content`` en vez del array ``tool_calls``
nativo. El adapter lo normaliza con la estrategia parse → 1 retry → strip:

  1. Parse exitoso → tool_calls recuperadas SIN re-llamar al modelo.
  2. Parse falla → 1 retry.
  3. Retry sigue roto → se stripea el DSML y se devuelve texto legible.

El core nunca ve la basura — es el adapter traduciendo el I/O roto del provider.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.outbound.providers.base import ResolvedLLMConfig
from adapters.outbound.providers.deepseek import (
    DeepSeekProvider,
    _coerce_param,
    _has_dsml,
    _parse_dsml_tool_calls,
    _strip_dsml,
)
from core.domain.entities.message import Message, Role

# Markup DSML real reportado por el usuario (｜ es FULLWIDTH VERTICAL LINE U+FF5C).
DSML_INBOX = (
    "<｜｜DSML｜｜tool_calls>\n"
    '<｜｜DSML｜｜invoke name="exchange_mail">\n'
    '<｜｜DSML｜｜parameter name="operation" string="true">list_inbox</｜｜DSML｜｜parameter>\n'
    '<｜｜DSML｜｜parameter name="limit" string="false">5</｜｜DSML｜｜parameter>\n'
    "</｜｜DSML｜｜invoke>\n"
    "</｜｜DSML｜｜tool_calls>"
)

# DSML "roto": trae el substring DSML (heurística positiva) pero sin un bloque
# ``invoke`` parseable → ``_parse_dsml_tool_calls`` devuelve [].
DSML_MALFORMED = "<｜｜DSML｜｜tool_calls>ups, truncado</｜｜DSML｜｜tool_cal"


def _cfg() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider="deepseek",
        model="deepseek-v4-pro",
        temperature=0.7,
        max_tokens=1024,
        api_key="sk-test",
        reasoning_effort="high",
    )


def _fake_message_response(message: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": message}]}
    resp.raise_for_status = MagicMock()
    return resp


def _fake_client(*responses: MagicMock) -> AsyncMock:
    """Cliente httpx mockeado. Con N responses, ``post`` las va devolviendo en orden."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    if len(responses) == 1:
        client.post.return_value = responses[0]
    else:
        client.post.side_effect = list(responses)
    return client


# ---------------------------------------------------------------------------
# Parser puro
# ---------------------------------------------------------------------------


def test_parse_extracts_tool_call_with_openai_shape() -> None:
    calls = _parse_dsml_tool_calls(DSML_INBOX)

    assert len(calls) == 1
    tc = calls[0]
    assert tc["id"] == "call_dsml_0"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "exchange_mail"
    # arguments es JSON string (lo que el tool loop y la re-serialización esperan).
    assert isinstance(tc["function"]["arguments"], str)


def test_parse_coerces_types_per_string_attribute() -> None:
    """``string="true"`` preserva string; ``string="false"`` coerce a nativo."""
    args = json.loads(_parse_dsml_tool_calls(DSML_INBOX)[0]["function"]["arguments"])

    assert args["operation"] == "list_inbox"  # string="true" → string
    assert args["limit"] == 5  # string="false" → int
    assert isinstance(args["limit"], int)


def test_parse_multiple_invokes_get_unique_ids() -> None:
    double = DSML_INBOX + "\n" + DSML_INBOX.replace("exchange_mail", "web_search")
    calls = _parse_dsml_tool_calls(double)

    assert [c["id"] for c in calls] == ["call_dsml_0", "call_dsml_1"]
    assert calls[1]["function"]["name"] == "web_search"


def test_parse_malformed_returns_empty() -> None:
    assert _parse_dsml_tool_calls(DSML_MALFORMED) == []


def test_coerce_param_respects_string_flag() -> None:
    assert _coerce_param("5", "true") == "5"
    assert _coerce_param("5", "false") == 5
    assert _coerce_param("3.14", "false") == 3.14
    assert _coerce_param("true", "false") is True
    assert _coerce_param("null", "false") is None
    assert _coerce_param("list_inbox", "false") == "list_inbox"


def test_has_dsml_heuristic() -> None:
    assert _has_dsml(DSML_INBOX) is True
    assert _has_dsml("respuesta normal del assistant") is False


def test_strip_dsml_leaves_readable_text() -> None:
    content = "Acá tenés:\n" + DSML_INBOX
    cleaned = _strip_dsml(content)

    assert "DSML" not in cleaned
    assert cleaned.startswith("Acá tenés:")


# ---------------------------------------------------------------------------
# complete() — camino 1: parse exitoso, SIN retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_recovers_dsml_without_retry() -> None:
    provider = DeepSeekProvider(_cfg())
    client = _fake_client(_fake_message_response({"content": DSML_INBOX, "tool_calls": None}))

    with patch("adapters.outbound.providers.deepseek.httpx.AsyncClient", return_value=client):
        result = await provider.complete([Message(role=Role.USER, content="leé mi mail")], "sys")

    # Una sola llamada: el parse NO dispara retry.
    assert client.post.call_count == 1
    assert result.text == ""  # acá no hay texto fuera del bloque DSML → text vacío
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["function"]["name"] == "exchange_mail"


@pytest.mark.asyncio
async def test_complete_preserves_narration_around_dsml_block() -> None:
    """La tool call puede venir EN MEDIO del mensaje: el texto que la rodea
    (narración tipo "Voy a buscar...") se preserva en text_blocks; solo el bloque
    DSML se stripea. El tool loop emite esa narración antes de ejecutar la tool."""
    provider = DeepSeekProvider(_cfg())
    content = "Voy a intentar directamente:\n\n" + DSML_INBOX
    client = _fake_client(_fake_message_response({"content": content, "tool_calls": None}))

    with patch("adapters.outbound.providers.deepseek.httpx.AsyncClient", return_value=client):
        result = await provider.complete([Message(role=Role.USER, content="leé mi mail")], "sys")

    assert client.post.call_count == 1
    assert result.text == "Voy a intentar directamente:"  # narración conservada
    assert "DSML" not in result.text
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["function"]["name"] == "exchange_mail"


# ---------------------------------------------------------------------------
# complete() — camino 2: parse falla → 1 retry → tool_calls limpios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_retries_once_when_parse_fails() -> None:
    provider = DeepSeekProvider(_cfg())
    clean_tc = [
        {
            "id": "call_0",
            "type": "function",
            "function": {"name": "exchange_mail", "arguments": '{"operation": "list_inbox"}'},
        }
    ]
    client = _fake_client(
        _fake_message_response({"content": DSML_MALFORMED, "tool_calls": None}),
        _fake_message_response({"content": "", "tool_calls": clean_tc}),
    )

    with (
        patch("adapters.outbound.providers.deepseek.httpx.AsyncClient", return_value=client),
        patch("adapters.outbound.providers.deepseek.asyncio.sleep", new=AsyncMock()) as sleep,
    ):
        result = await provider.complete([Message(role=Role.USER, content="leé mi mail")], "sys")

    assert client.post.call_count == 2  # malformado → retry
    sleep.assert_awaited_once()
    assert result.tool_calls == clean_tc


# ---------------------------------------------------------------------------
# complete() — camino 3: DSML persiste tras retry → strip, texto legible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_strips_when_dsml_persists_after_retry() -> None:
    provider = DeepSeekProvider(_cfg())
    client = _fake_client(
        _fake_message_response({"content": DSML_MALFORMED, "tool_calls": None}),
        _fake_message_response({"content": "Perdón: " + DSML_MALFORMED, "tool_calls": None}),
    )

    with (
        patch("adapters.outbound.providers.deepseek.httpx.AsyncClient", return_value=client),
        patch("adapters.outbound.providers.deepseek.asyncio.sleep", new=AsyncMock()),
    ):
        result = await provider.complete([Message(role=Role.USER, content="leé mi mail")], "sys")

    assert client.post.call_count == 2
    assert result.tool_calls == []
    assert "DSML" not in result.text
    assert result.text.startswith("Perdón:")


@pytest.mark.asyncio
async def test_complete_retry_recovers_parseable_dsml() -> None:
    """Si el retry vuelve a emitir DSML pero esta vez parseable, se recupera."""
    provider = DeepSeekProvider(_cfg())
    client = _fake_client(
        _fake_message_response({"content": DSML_MALFORMED, "tool_calls": None}),
        _fake_message_response({"content": DSML_INBOX, "tool_calls": None}),
    )

    with (
        patch("adapters.outbound.providers.deepseek.httpx.AsyncClient", return_value=client),
        patch("adapters.outbound.providers.deepseek.asyncio.sleep", new=AsyncMock()),
    ):
        result = await provider.complete([Message(role=Role.USER, content="leé mi mail")], "sys")

    assert client.post.call_count == 2
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["function"]["name"] == "exchange_mail"


# ---------------------------------------------------------------------------
# Regresión: respuesta normal (sin DSML) no se toca
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_normal_response_untouched() -> None:
    provider = DeepSeekProvider(_cfg())
    native_tc = [
        {
            "id": "call_0",
            "type": "function",
            "function": {"name": "web_search", "arguments": "{}"},
        }
    ]
    client = _fake_client(_fake_message_response({"content": "", "tool_calls": native_tc}))

    with patch("adapters.outbound.providers.deepseek.httpx.AsyncClient", return_value=client):
        result = await provider.complete([Message(role=Role.USER, content="buscá")], "sys")

    assert client.post.call_count == 1
    assert result.tool_calls == native_tc
