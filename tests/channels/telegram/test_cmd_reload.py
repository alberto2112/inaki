"""``/reload`` de Telegram pide el reload manual y avisa qué descarta (config-set-efimero).

Extiende un slash existente (panel del OPERADOR, determinista, sin LLM). Pide el reload
ANTES de responder para poder informar los descartados; el drenaje de ``IChannel.stop()``
espera a este handler en vuelo, así que la respuesta llega igual.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inaki.channels.telegram.bot import TelegramBot
from inaki.channels.telegram.ports import TelegramChannelSettings


def _bot(reloader) -> TelegramBot:
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.telegram = TelegramChannelSettings(
        token="dummy-token", allowed_user_ids=("12345",), reactions=False
    )
    with patch("inaki.channels.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        return TelegramBot(settings=cfg, ports=MagicMock(), reloader=reloader)


def _update(user_id: int = 12345) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture
def reloader() -> MagicMock:
    r = MagicMock()
    r.request_reload.return_value = []
    return r


async def test_reload_responde_con_los_descartados(reloader):
    reloader.request_reload.return_value = ["dev: llm.model = 'x'", "dev: llm.temperature = 0.3"]
    update = _update()

    await _bot(reloader)._commands.cmd_reload(update, MagicMock())

    reloader.request_reload.assert_called_once_with()
    update.message.reply_text.assert_awaited_once()
    texto = update.message.reply_text.await_args.args[0]
    assert texto.startswith("Reiniciando daemon...")
    assert "Se descartan los cambios de runtime" in texto
    assert "dev: llm.model = 'x'" in texto
    assert "dev: llm.temperature = 0.3" in texto


async def test_reload_sin_descartados_responde_el_mensaje_de_siempre(reloader):
    update = _update()

    await _bot(reloader)._commands.cmd_reload(update, MagicMock())

    reloader.request_reload.assert_called_once_with()
    update.message.reply_text.assert_awaited_once_with("Reiniciando daemon...")


async def test_reload_de_usuario_no_listado_no_hace_nada(reloader):
    update = _update(user_id=999)

    await _bot(reloader)._commands.cmd_reload(update, MagicMock())

    reloader.request_reload.assert_not_called()
    update.message.reply_text.assert_not_awaited()


async def test_reload_sin_reloader_avisa_que_no_esta_disponible():
    update = _update()

    await _bot(None)._commands.cmd_reload(update, MagicMock())

    texto = update.message.reply_text.await_args.args[0]
    assert "Reload no disponible" in texto
