"""Tests para SendTelegramMessageTool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from adapters.inbound.telegram.tools.send_telegram_message_tool import (
    SendTelegramMessageTool,
)
from core.domain.entities.message import Role


@pytest.fixture
def sender() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def history() -> AsyncMock:
    return AsyncMock()


def _make_tool(
    sender: AsyncMock,
    history: AsyncMock | None = None,
    *,
    agent_id: str = "test-agent",
) -> SendTelegramMessageTool:
    return SendTelegramMessageTool(
        sender=sender,
        history=history or AsyncMock(),
        agent_id=agent_id,
    )


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


async def test_envia_mensaje(sender):
    tool = _make_tool(sender)

    result = await tool.execute(chat_id="123456789", text="hola che")

    assert result.success is True
    payload = json.loads(result.output)
    assert payload == {"sent": True, "chat_id": "123456789"}
    sender.send_message.assert_awaited_once_with(chat_id="123456789", text="hola che")


async def test_recorta_espacios(sender):
    tool = _make_tool(sender)

    await tool.execute(chat_id="  123  ", text="  con espacios  ")

    sender.send_message.assert_awaited_once_with(chat_id="123", text="con espacios")


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


async def test_falla_sin_chat_id(sender):
    tool = _make_tool(sender)
    result = await tool.execute(text="hola")
    assert result.success is False
    assert result.retryable is False
    assert "chat_id" in result.error.lower()
    sender.send_message.assert_not_awaited()


async def test_falla_sin_text(sender):
    tool = _make_tool(sender)
    result = await tool.execute(chat_id="123")
    assert result.success is False
    assert result.retryable is False
    assert "text" in result.error.lower()
    sender.send_message.assert_not_awaited()


async def test_falla_text_vacio(sender):
    tool = _make_tool(sender)
    result = await tool.execute(chat_id="123", text="   ")
    assert result.success is False
    sender.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Errores de transport
# ---------------------------------------------------------------------------


async def test_value_error_del_sender_no_retryable(sender):
    sender.send_message.side_effect = ValueError("chat id mal")
    tool = _make_tool(sender)
    result = await tool.execute(chat_id="abc", text="hola")
    assert result.success is False
    assert result.retryable is False


async def test_transport_timeout_es_retryable(sender):
    sender.send_message.side_effect = TimeoutError("timeout")
    tool = _make_tool(sender)
    result = await tool.execute(chat_id="123", text="hola")
    assert result.success is False
    assert result.retryable is True


# ---------------------------------------------------------------------------
# Persistencia en historial — bajo el scope del chat DESTINO
# ---------------------------------------------------------------------------


async def test_persiste_en_scope_del_destino(sender, history):
    tool = _make_tool(sender, history)

    await tool.execute(chat_id="999", text="aviso importante")

    history.append.assert_awaited_once()
    args, kwargs = history.append.call_args
    assert args[0] == "test-agent"
    msg = args[1]
    assert msg.role == Role.ASSISTANT
    assert msg.content == "aviso importante"
    assert kwargs.get("channel") == "telegram"
    assert kwargs.get("chat_id") == "999"


async def test_no_persiste_si_falla_el_envio(sender, history):
    sender.send_message.side_effect = TimeoutError("timeout")
    tool = _make_tool(sender, history)

    await tool.execute(chat_id="999", text="aviso")

    history.append.assert_not_awaited()
