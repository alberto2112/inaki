"""Tests para el arranque del bot de Telegram en el daemon runner.

El daemon NO usa ``Application.run_polling`` (no dispara el hook ``post_init``):
maneja el lifecycle a mano con ``async with app`` + ``start()`` +
``updater.start_polling``. Por eso el aviso 'online' al volver de offline se
invoca explícitamente desde ``_run_telegram_bot``. Estos tests blindan ese
wiring — su ausencia dejó pasar un bug donde el aviso nunca se ejecutaba.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.run(coro)


def _make_mock_bot() -> tuple[MagicMock, MagicMock, asyncio.Event]:
    """Bot mockeado con un Application que soporta ``async with`` y un updater.

    Devuelve (bot, updater, polling_started). ``polling_started`` se setea cuando
    ``updater.start_polling`` es invocado, para sincronizar el test sin sleeps.
    """
    polling_started = asyncio.Event()

    updater = MagicMock()

    async def _fake_start_polling(**_kwargs):
        polling_started.set()

    updater.start_polling = AsyncMock(side_effect=_fake_start_polling)
    updater.stop = AsyncMock()

    app = MagicMock()
    app.__aenter__ = AsyncMock(return_value=app)
    app.__aexit__ = AsyncMock(return_value=None)
    app.start = AsyncMock()
    app.stop = AsyncMock()
    app.updater = updater

    bot = MagicMock()
    bot._app = app
    bot._announce_back_online = AsyncMock()
    bot.setup_commands = AsyncMock()
    bot.verificar_bot_username = AsyncMock()
    bot.subscribe_broadcast_trigger = AsyncMock()

    return bot, updater, polling_started


async def _drive_until_polling(bot, polling_started) -> None:
    """Corre _run_telegram_bot hasta que arranca el polling y luego lo cancela."""
    from inaki import daemon_runner

    agent_cfg = MagicMock()
    agent_cfg.id = "test-agent"
    app_container = MagicMock()

    with (
        patch("adapters.inbound.telegram.bot.TelegramBot", return_value=bot),
        patch("infrastructure.container.build_telegram_bot_settings", return_value=MagicMock()),
        patch("infrastructure.container.build_telegram_bot_ports", return_value=MagicMock()),
    ):
        task = asyncio.create_task(
            daemon_runner._run_telegram_bot(agent_cfg, MagicMock(), app_container)
        )
        await asyncio.wait_for(polling_started.wait(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_daemon_invoca_aviso_online_al_arrancar() -> None:
    """_run_telegram_bot DEBE invocar _announce_back_online (con el Application del bot)."""
    bot, _updater, polling_started = _make_mock_bot()

    _run(_drive_until_polling(bot, polling_started))

    bot._announce_back_online.assert_awaited_once_with(bot._app)


def test_polling_arranca_sin_descartar_backlog() -> None:
    """El polling debe arrancar con drop_pending_updates=False (el aviso ya drenó el backlog)."""
    bot, updater, polling_started = _make_mock_bot()

    _run(_drive_until_polling(bot, polling_started))

    updater.start_polling.assert_awaited_once()
    assert updater.start_polling.await_args.kwargs["drop_pending_updates"] is False


def test_aviso_se_invoca_antes_del_polling() -> None:
    """El aviso debe drenar el backlog ANTES de que el updater empiece a hacer polling."""
    bot, updater, polling_started = _make_mock_bot()

    # Registramos el orden real de invocación en un manager compartido.
    orden: list[str] = []
    bot._announce_back_online = AsyncMock(side_effect=lambda *_a, **_k: orden.append("announce"))

    async def _start_polling(**_kwargs):
        orden.append("polling")
        polling_started.set()

    updater.start_polling = AsyncMock(side_effect=_start_polling)

    _run(_drive_until_polling(bot, polling_started))

    assert orden == ["announce", "polling"]
