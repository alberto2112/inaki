"""Camino dorado 2: un mensaje de Telegram atraviesa el canal hasta el historial.

Protege lo que las fases 3 (egress único) y 4 (canal Telegram vertical) mueven:
el ``TelegramBot`` real, armado con los builders reales del composition root,
recibe un ``Update`` de chat privado, corre el turno con el agente REAL y
responde al chat; el historial queda en el scope ``(telegram, chat_id)``.

Solo se falsea la API de Telegram (``Application`` de PTB y el ``Update``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import AGENT_ID, RESPUESTA_LLM, USER_ID, FakeLLM


def _update_privado(texto: str) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = int(USER_ID)
    update.effective_chat.id = int(USER_ID)
    update.effective_chat.type = "private"
    update.message.chat.id = int(USER_ID)
    update.message.chat.type = "private"
    update.message.text = texto
    update.message.location = None
    update.message.from_user.id = int(USER_ID)
    update.message.from_user.username = "alberto"
    update.message.from_user.first_name = "Alberto"
    update.message.from_user.last_name = None
    update.message.reply_text = AsyncMock(return_value=None)
    update.message.set_reaction = AsyncMock(return_value=None)
    return update


@pytest.fixture
def bot(app_container):
    """``TelegramBot`` real con settings y ports salidos de los builders del container."""
    from inaki.channels.telegram.bot import TelegramBot
    from inaki.channels.telegram.wiring import build_telegram_bot_ports, build_telegram_bot_settings

    agente = app_container.get_agent(AGENT_ID)
    with patch("inaki.channels.telegram.bot.Application"):
        return TelegramBot(
            build_telegram_bot_settings(agente.agent_config),
            build_telegram_bot_ports(agente),
        )


async def test_mensaje_privado_responde_y_persiste(
    home: Path, bot, bordes_externos: FakeLLM
) -> None:
    update = _update_privado("¿qué hora es?")

    await bot._handle_message(update, context=None)

    update.message.reply_text.assert_awaited()
    texto_enviado = update.message.reply_text.await_args.args[0]
    assert RESPUESTA_LLM in texto_enviado

    llamada = bordes_externos.llamadas[0]
    assert llamada["messages"][-1].content == "¿qué hora es?"
    # La identidad del remitente llega al prompt vía {{CHANNEL.*}}.
    assert "Alberto" in llamada["system_prompt"]

    with sqlite3.connect(home / "data" / "history.db") as conn:
        filas = conn.execute(
            "SELECT role, content, channel, chat_id FROM history ORDER BY id"
        ).fetchall()
    assert filas == [
        ("user", "¿qué hora es?", "telegram", USER_ID),
        ("assistant", RESPUESTA_LLM, "telegram", USER_ID),
    ]


async def test_usuario_no_autorizado_no_dispara_turno(bot, bordes_externos: FakeLLM) -> None:
    update = _update_privado("hola")
    update.effective_user.id = 999
    update.message.from_user.id = 999

    await bot._handle_message(update, context=None)

    update.message.reply_text.assert_not_awaited()
    assert bordes_externos.llamadas == []
