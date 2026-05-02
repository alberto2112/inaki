"""TelegramFileSender — adapter de :class:`IFileSender` sobre python-telegram-bot.

Resuelve el bot de forma perezosa vía un callable inyectado, igual que el
viejo ``TelegramPhotoSender``. Mismo patrón que ``TelegramSink``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from core.domain.value_objects.telegram_file import FileContentType
from core.ports.outbound.file_sender_port import IFileSender

logger = logging.getLogger(__name__)


class TelegramFileSender(IFileSender):
    def __init__(self, get_telegram_bot: Callable[[], object | None]) -> None:
        self._get_telegram_bot = get_telegram_bot

    async def send(
        self,
        *,
        chat_id: str,
        content_type: FileContentType,
        source: Path,
        caption: str | None = None,
    ) -> None:
        bot = self._require_bot()
        chat_id_int = self._parse_chat_id(chat_id)
        if not source.exists():
            raise FileNotFoundError(f"El fichero no existe: {source}")

        # Cada content_type usa un método distinto del bot; mantenemos el handle
        # abierto y nos aseguramos de cerrarlo en finally.
        handle = source.open("rb")
        try:
            if content_type == "photo":
                await bot.send_photo(  # type: ignore[attr-defined]
                    chat_id=chat_id_int, photo=handle, caption=caption
                )
            elif content_type == "audio":
                await bot.send_audio(  # type: ignore[attr-defined]
                    chat_id=chat_id_int, audio=handle, caption=caption
                )
            elif content_type == "video":
                await bot.send_video(  # type: ignore[attr-defined]
                    chat_id=chat_id_int, video=handle, caption=caption
                )
            elif content_type == "file":
                await bot.send_document(  # type: ignore[attr-defined]
                    chat_id=chat_id_int, document=handle, caption=caption
                )
            else:  # pragma: no cover — Literal exhaustivo
                raise ValueError(f"content_type no soportado: {content_type!r}")
        finally:
            handle.close()

    async def send_album(
        self,
        *,
        chat_id: str,
        sources: list[Path],
        caption: str | None = None,
    ) -> None:
        if not sources:
            raise ValueError("send_album requiere al menos una foto")
        if len(sources) == 1:
            await self.send(
                chat_id=chat_id,
                content_type="photo",
                source=sources[0],
                caption=caption,
            )
            return

        bot = self._require_bot()
        chat_id_int = self._parse_chat_id(chat_id)

        # python-telegram-bot pide objetos InputMediaPhoto. Importamos lazy
        # para no atar el dominio al módulo de telegram.
        from telegram import InputMediaPhoto

        for path in sources:
            if not path.exists():
                raise FileNotFoundError(f"El fichero no existe: {path}")

        handles = [path.open("rb") for path in sources]
        try:
            media = [
                InputMediaPhoto(media=handles[0], caption=caption),
                *(InputMediaPhoto(media=h) for h in handles[1:]),
            ]
            await bot.send_media_group(  # type: ignore[attr-defined]
                chat_id=chat_id_int, media=media
            )
        finally:
            for h in handles:
                h.close()

    def _require_bot(self) -> object:
        bot = self._get_telegram_bot()
        if bot is None:
            raise RuntimeError(
                "Telegram no está disponible: no hay un bot registrado en el sistema."
            )
        return bot

    @staticmethod
    def _parse_chat_id(chat_id: str) -> int:
        try:
            return int(chat_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"chat_id debe ser un entero serializado: {chat_id!r}") from exc
