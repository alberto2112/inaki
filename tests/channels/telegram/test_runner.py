"""``TelegramChannel``: el ciclo de vida del bot y el broadcast bajo ``IChannel``.

El daemon NO usa ``Application.run_polling`` (no dispara ``post_init``): el canal
maneja el lifecycle a mano. Estos tests blindan el ORDEN — su ausencia dejó pasar
un bug donde el aviso 'online' nunca se ejecutaba.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from inaki.channels.telegram.channel import TelegramChannel


def _make_mock_bot() -> tuple[MagicMock, MagicMock, list[str]]:
    """Bot mockeado con un Application y un updater que registran el orden real."""
    orden: list[str] = []
    updater = MagicMock()
    updater.running = True
    updater.start_polling = AsyncMock(side_effect=lambda **_k: orden.append("polling"))
    updater.stop = AsyncMock(side_effect=lambda: orden.append("updater.stop"))

    app = MagicMock()
    app.running = True
    app.initialize = AsyncMock(side_effect=lambda: orden.append("initialize"))
    app.start = AsyncMock(side_effect=lambda: orden.append("start"))
    app.stop = AsyncMock(side_effect=lambda: orden.append("stop"))
    app.shutdown = AsyncMock(side_effect=lambda: orden.append("shutdown"))
    app.updater = updater

    bot = MagicMock()
    bot.application = app
    bot.announce_back_online = AsyncMock(side_effect=lambda: orden.append("announce"))
    bot.setup_commands = AsyncMock(side_effect=lambda: orden.append("commands"))
    bot.verificar_bot_username = AsyncMock(side_effect=lambda: orden.append("username"))
    bot.subscribe_broadcast_trigger = AsyncMock(side_effect=lambda: orden.append("trigger"))
    return bot, updater, orden


async def test_start_respeta_el_orden_y_el_aviso_va_antes_del_polling() -> None:
    bot, updater, orden = _make_mock_bot()

    await TelegramChannel("a", bot).start()

    assert orden == [
        "initialize",
        "start",
        "commands",
        "username",
        "trigger",
        "announce",
        "polling",
    ]
    # El aviso ya drenó el backlog: el polling NO lo descarta.
    assert updater.start_polling.await_args.kwargs["drop_pending_updates"] is False


async def test_stop_apaga_updater_app_y_shutdown() -> None:
    bot, _updater, orden = _make_mock_bot()
    canal = TelegramChannel("a", bot)
    await canal.start()
    orden.clear()

    await canal.stop()

    assert orden == ["updater.stop", "stop", "shutdown"]


async def test_stop_sin_start_es_no_op() -> None:
    bot, _updater, orden = _make_mock_bot()

    await TelegramChannel("a", bot).stop()

    assert orden == []


async def test_broadcast_arranca_antes_del_bot_y_se_detiene_despues() -> None:
    bot, _updater, orden = _make_mock_bot()
    broadcast = MagicMock()
    broadcast.role, broadcast.host, broadcast.port = "server", "0.0.0.0", 6499
    broadcast.start = AsyncMock(side_effect=lambda: orden.append("broadcast.start"))
    broadcast.stop = AsyncMock(side_effect=lambda: orden.append("broadcast.stop"))
    canal = TelegramChannel("a", bot, broadcast)

    await canal.start()
    assert orden[0] == "broadcast.start" and orden[-1] == "polling"
    orden.clear()

    await canal.stop()
    assert orden[-1] == "broadcast.stop"


async def test_bind_fallido_del_broadcast_no_impide_el_bot(caplog) -> None:
    """``broadcast-arranque-observable``: el error se ve como startup.resource, el bot arranca."""
    import logging

    bot, _updater, orden = _make_mock_bot()
    broadcast = MagicMock()
    broadcast.start = AsyncMock(side_effect=OSError("address already in use"))
    broadcast.stop = AsyncMock()

    with caplog.at_level(logging.ERROR):
        await TelegramChannel("a", bot, broadcast).start()

    assert "polling" in orden
    errores = [r for r in caplog.records if getattr(r, "resource", None) == "broadcast"]
    assert errores and errores[0].status == "error"
    assert "address already in use" in errores[0].reason
