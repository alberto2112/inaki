"""Tests para TelegramMessageSender."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from adapters.outbound.messaging.telegram_message_sender import TelegramMessageSender


@pytest.fixture
def fake_bot() -> AsyncMock:
    return AsyncMock()


def _sender(bot) -> TelegramMessageSender:
    return TelegramMessageSender(get_telegram_bot=lambda: bot)


async def test_send_message_llama_al_bot(fake_bot):
    await _sender(fake_bot).send_message(chat_id="-100", text="hola che")

    fake_bot.send_message.assert_awaited_once_with(chat_id=-100, text="hola che")


async def test_chat_id_no_entero_lanza_value_error(fake_bot):
    with pytest.raises(ValueError, match="entero"):
        await _sender(fake_bot).send_message(chat_id="abc", text="hola")
    fake_bot.send_message.assert_not_awaited()


async def test_texto_vacio_lanza_value_error(fake_bot):
    with pytest.raises(ValueError, match="vacío"):
        await _sender(fake_bot).send_message(chat_id="123", text="   ")
    fake_bot.send_message.assert_not_awaited()


async def test_sin_bot_lanza_runtime_error():
    sender = TelegramMessageSender(get_telegram_bot=lambda: None)
    with pytest.raises(RuntimeError, match="Telegram no está disponible"):
        await sender.send_message(chat_id="123", text="hola")
