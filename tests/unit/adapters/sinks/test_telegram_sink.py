"""Tests para TelegramSink — migración de la lógica actual de ChannelSenderAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from adapters.outbound.sinks.telegram_sink import TelegramSink
from core.domain.value_objects.dispatch_result import DispatchResult


def test_telegram_sink_tiene_prefix_telegram() -> None:
    assert TelegramSink.prefix == "telegram"


async def test_telegram_sink_delega_al_bot_lazy() -> None:
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    sink = TelegramSink(get_telegram_bot=lambda: bot)
    await sink.send("telegram:12345", "hola")
    bot.send_message.assert_awaited_once_with(12345, "hola")


async def test_telegram_sink_retorna_dispatch_result() -> None:
    bot = AsyncMock()
    sink = TelegramSink(get_telegram_bot=lambda: bot)
    result = await sink.send("telegram:999", "x")
    assert isinstance(result, DispatchResult)
    assert result.original_target == "telegram:999"
    assert result.resolved_target == "telegram:999"


async def test_telegram_sink_sin_bot_lanza_valueerror() -> None:
    sink = TelegramSink(get_telegram_bot=lambda: None)
    with pytest.raises(ValueError, match="Telegram"):
        await sink.send("telegram:1", "x")


async def test_telegram_sink_target_sin_prefix_telegram_lanza() -> None:
    bot = AsyncMock()
    sink = TelegramSink(get_telegram_bot=lambda: bot)
    with pytest.raises(ValueError, match="telegram:"):
        await sink.send("file:///tmp/x", "x")
