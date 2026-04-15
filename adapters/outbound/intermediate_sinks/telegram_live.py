"""TelegramLiveIntermediateSink — empuja cada intermedio como mensaje Telegram.

A diferencia del ``BufferingIntermediateSink`` (pensado para REST/CLI), este
sink emite en vivo: cada bloque de texto que el LLM genera junto con
tool_calls se envía como un mensaje de Telegram independiente mientras las
tools corren. El mensaje final del turno sigue viajando por el return de
``RunAgentUseCase.execute()`` y lo envía el handler del bot.
"""

from __future__ import annotations

import logging

from core.ports.outbound.intermediate_sink_port import IIntermediateSink

logger = logging.getLogger(__name__)


class TelegramLiveIntermediateSink(IIntermediateSink):
    """Sink que reenvía cada intermedio al chat de Telegram original."""

    def __init__(self, bot: object, chat_id: int) -> None:
        """
        Args:
            bot: Instancia de ``TelegramBot`` con ``send_message(chat_id, text)``.
            chat_id: Chat destino — el mismo del usuario que inició el turno.
        """
        self._bot = bot
        self._chat_id = chat_id

    async def emit(self, text: str) -> None:
        try:
            await self._bot.send_message(self._chat_id, text)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover — logging defensivo
            # No queremos que un fallo de red corte el loop de tools.
            logger.warning(
                "TelegramLiveIntermediateSink: fallo enviando intermedio a chat %s: %s",
                self._chat_id,
                exc,
            )
