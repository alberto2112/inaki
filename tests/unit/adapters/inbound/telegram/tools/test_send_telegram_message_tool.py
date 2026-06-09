"""Tests para SendTelegramMessageTool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from adapters.inbound.telegram.tools.send_telegram_message_tool import (
    SendTelegramMessageTool,
)
from adapters.outbound.messaging.channel_outbound_registry import ChannelOutboundRegistry
from core.domain.value_objects.outbound_kind import OutboundKind


def _make_registry(adapter: AsyncMock | None = None) -> ChannelOutboundRegistry:
    registry = ChannelOutboundRegistry()
    if adapter is not None:
        registry.register(adapter)
    return registry


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.channel_name = "telegram"
    adapter.capabilities.return_value = {OutboundKind.TEXT}
    adapter.send = AsyncMock()
    return adapter


def _make_tool(registry: ChannelOutboundRegistry) -> SendTelegramMessageTool:
    return SendTelegramMessageTool(registry=registry)


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


async def test_envia_mensaje():
    adapter = _make_adapter()
    tool = _make_tool(_make_registry(adapter))

    result = await tool.execute(chat_id="123456789", text="hola che")

    assert result.success is True
    payload = json.loads(result.output)
    assert payload == {"sent": True, "chat_id": "123456789"}
    adapter.send.assert_awaited_once_with(
        chat_id="123456789", kind=OutboundKind.TEXT, text="hola che"
    )


async def test_recorta_espacios():
    adapter = _make_adapter()
    tool = _make_tool(_make_registry(adapter))

    await tool.execute(chat_id="  123  ", text="  con espacios  ")

    adapter.send.assert_awaited_once_with(
        chat_id="123", kind=OutboundKind.TEXT, text="con espacios"
    )


# ---------------------------------------------------------------------------
# Validación de parámetros
# ---------------------------------------------------------------------------


async def test_falla_sin_chat_id():
    adapter = _make_adapter()
    tool = _make_tool(_make_registry(adapter))
    result = await tool.execute(text="hola")
    assert result.success is False
    assert result.retryable is False
    assert "chat_id" in result.error.lower()
    adapter.send.assert_not_awaited()


async def test_falla_sin_text():
    adapter = _make_adapter()
    tool = _make_tool(_make_registry(adapter))
    result = await tool.execute(chat_id="123")
    assert result.success is False
    assert result.retryable is False
    assert "text" in result.error.lower()
    adapter.send.assert_not_awaited()


async def test_falla_text_vacio():
    adapter = _make_adapter()
    tool = _make_tool(_make_registry(adapter))
    result = await tool.execute(chat_id="123", text="   ")
    assert result.success is False
    adapter.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Canal no registrado → falla no retryable
# ---------------------------------------------------------------------------


async def test_falla_canal_no_registrado():
    # Registry vacío: ningún adapter registrado
    tool = _make_tool(_make_registry())
    result = await tool.execute(chat_id="123", text="hola")
    assert result.success is False
    assert result.retryable is False
    assert "telegram" in result.error.lower()


# ---------------------------------------------------------------------------
# Errores de transport
# ---------------------------------------------------------------------------


async def test_value_error_del_adapter_no_retryable():
    adapter = _make_adapter()
    adapter.send.side_effect = ValueError("chat id mal")
    tool = _make_tool(_make_registry(adapter))
    result = await tool.execute(chat_id="abc", text="hola")
    assert result.success is False
    assert result.retryable is False


async def test_transport_timeout_es_retryable():
    adapter = _make_adapter()
    adapter.send.side_effect = TimeoutError("timeout")
    tool = _make_tool(_make_registry(adapter))
    result = await tool.execute(chat_id="123", text="hola")
    assert result.success is False
    assert result.retryable is True


# ---------------------------------------------------------------------------
# La tool NO persiste directamente en historial — lo hace el adapter
# ---------------------------------------------------------------------------


async def test_no_llama_history_directamente():
    """La tool ya no tiene _history. Verificamos que adapter.send es suficiente."""
    adapter = _make_adapter()
    tool = _make_tool(_make_registry(adapter))

    result = await tool.execute(chat_id="999", text="aviso importante")

    assert result.success is True
    # El adapter recibió el send — él persiste internamente
    adapter.send.assert_awaited_once()
    # La tool no tiene atributo _history ni _agent_id
    assert not hasattr(tool, "_history")
    assert not hasattr(tool, "_agent_id")


async def test_no_llama_send_si_falla_validacion():
    adapter = _make_adapter()
    adapter.send.side_effect = TimeoutError("timeout")
    tool = _make_tool(_make_registry(adapter))

    await tool.execute(chat_id="999", text="aviso")

    # send fue llamado (y falló), pero en el fallo de validación no se llama:
    # re-testeamos con validación que falla antes de llegar al adapter
    adapter2 = _make_adapter()
    tool2 = _make_tool(_make_registry(adapter2))
    await tool2.execute(chat_id="", text="aviso")
    adapter2.send.assert_not_awaited()
