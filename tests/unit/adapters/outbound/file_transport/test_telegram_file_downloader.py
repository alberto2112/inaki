"""Tests para TelegramFileDownloader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.outbound.file_transport.telegram_file_downloader import (
    TelegramFileDownloader,
)


def _wrap_bot(ptb_bot) -> MagicMock:
    """Imita la estructura del TelegramBot real (._app.bot)."""
    wrapper = MagicMock()
    wrapper._app = MagicMock()
    wrapper._app.bot = ptb_bot
    return wrapper


@pytest.fixture
def ptb_bot() -> AsyncMock:
    bot = AsyncMock()

    file_obj = AsyncMock()
    bot.get_file.return_value = file_obj
    return bot


async def test_descarga_creando_carpetas(ptb_bot, tmp_path):
    downloader = TelegramFileDownloader(get_telegram_bot=lambda: _wrap_bot(ptb_bot))
    dest = tmp_path / "sub" / "telegram" / "x.jpg"

    await downloader.download(file_id="F123", dest=dest)

    assert dest.parent.exists()
    ptb_bot.get_file.assert_awaited_once_with("F123")
    file_obj = ptb_bot.get_file.return_value
    file_obj.download_to_drive.assert_awaited_once_with(custom_path=str(dest))


async def test_falla_sin_bot(tmp_path):
    downloader = TelegramFileDownloader(get_telegram_bot=lambda: None)
    with pytest.raises(RuntimeError, match="bot"):
        await downloader.download(file_id="F", dest=tmp_path / "x.jpg")


