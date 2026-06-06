"""TelegramMessageSender — adapter de :class:`IMessageSender` sobre python-telegram-bot.

Resuelve el bot de forma perezosa vía un callable inyectado, mismo patrón que
``TelegramFileSender``.
"""

from __future__ import annotations

from collections.abc import Callable

from core.ports.outbound.message_sender_port import IMessageSender


class TelegramMessageSender(IMessageSender):
    def __init__(self, get_telegram_bot: Callable[[], object | None]) -> None:
        self._get_telegram_bot = get_telegram_bot

    async def send_message(self, *, chat_id: str, text: str) -> None:
        if not text.strip():
            raise ValueError("el texto del mensaje no puede ser vacío")
        bot = self._require_bot()
        chat_id_int = self._parse_chat_id(chat_id)
        await bot.send_message(chat_id=chat_id_int, text=text)  # type: ignore[attr-defined]

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
