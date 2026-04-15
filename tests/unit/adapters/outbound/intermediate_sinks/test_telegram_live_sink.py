"""Tests de TelegramLiveIntermediateSink."""

from __future__ import annotations

from unittest.mock import AsyncMock

from adapters.outbound.intermediate_sinks.telegram_live import (
    TelegramLiveIntermediateSink,
)


async def test_emit_delega_en_bot_send_message():
    bot = AsyncMock()
    sink = TelegramLiveIntermediateSink(bot=bot, chat_id=12345)

    await sink.emit("ok, voy a buscar esto")

    bot.send_message.assert_awaited_once_with(12345, "ok, voy a buscar esto")


async def test_emit_no_propaga_excepciones_del_bot():
    """Un fallo de red al enviar NO debe romper el tool loop — se traga y loggea."""
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("network down")
    sink = TelegramLiveIntermediateSink(bot=bot, chat_id=99)

    # No debe levantar
    await sink.emit("mensaje")


async def test_cada_emit_es_un_mensaje_nuevo():
    bot = AsyncMock()
    sink = TelegramLiveIntermediateSink(bot=bot, chat_id=7)

    await sink.emit("uno")
    await sink.emit("dos")

    assert bot.send_message.await_count == 2
    # Orden preservado
    calls = bot.send_message.await_args_list
    assert calls[0].args == (7, "uno")
    assert calls[1].args == (7, "dos")
