"""TelegramSink — delega en el bot de Telegram registrado en el container.

Migración directa de la lógica que antes vivía en ``ChannelSenderAdapter``:
el bot se resuelve lazy vía un callable inyectado, porque en el momento de
instanciar el sink (al levantar el container) todavía puede no haber ningún
bot vivo.
"""

from __future__ import annotations

from collections.abc import Callable

from core.domain.value_objects.dispatch_result import DispatchResult
from core.ports.outbound.outbound_sink_port import IOutboundSink


class TelegramSink(IOutboundSink):
    """Sink para targets ``telegram:<chat_id>``.

    Args:
        get_telegram_bot: Callable sin argumentos que devuelve el bot actual
            o ``None`` si no hay ninguno registrado. Se invoca en cada
            ``send`` (lazy) para tolerar el arranque diferido del daemon.
    """

    prefix = "telegram"

    def __init__(self, get_telegram_bot: Callable[[], object | None]) -> None:
        self._get_telegram_bot = get_telegram_bot

    async def send(self, target: str, text: str) -> DispatchResult:
        if not target.startswith("telegram:"):
            raise ValueError(
                f"TelegramSink espera target con prefix 'telegram:', recibió: '{target}'"
            )
        _, _, chat_id = target.partition(":")
        bot = self._get_telegram_bot()
        if bot is None:
            raise ValueError(
                "Telegram no está configurado. El bot no fue registrado en el sistema."
            )
        await bot.send_message(int(chat_id), text)  # type: ignore[attr-defined]
        return DispatchResult(original_target=target, resolved_target=target)
