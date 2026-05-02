"""TelegramFileDownloader — adapter de :class:`IFileDownloader`.

Resuelve el bot vía callable lazy (mismo patrón que el sender). Usa la API
de python-telegram-bot: ``await bot.get_file(file_id)`` devuelve un objeto
``File`` con método ``download_to_drive(dest)``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from core.ports.outbound.file_downloader_port import IFileDownloader

logger = logging.getLogger(__name__)


class TelegramFileDownloader(IFileDownloader):
    def __init__(self, get_telegram_bot: Callable[[], object | None]) -> None:
        self._get_telegram_bot = get_telegram_bot

    async def download(self, *, file_id: str, dest: Path) -> None:
        bot_wrapper = self._get_telegram_bot()
        if bot_wrapper is None:
            raise RuntimeError(
                "Telegram no está disponible: no hay un bot registrado en el sistema."
            )

        # bot_wrapper es nuestro TelegramBot; el bot real de python-telegram-bot
        # está en ._app.bot. Lo extraemos defensivamente para tolerar mocks.
        ptb_bot = getattr(bot_wrapper, "_app", None)
        ptb_bot = getattr(ptb_bot, "bot", None) if ptb_bot is not None else bot_wrapper

        dest.parent.mkdir(parents=True, exist_ok=True)

        file_obj = await ptb_bot.get_file(file_id)
        await file_obj.download_to_drive(custom_path=str(dest))
        logger.debug("file_id=%s descargado a %s", file_id, dest)
