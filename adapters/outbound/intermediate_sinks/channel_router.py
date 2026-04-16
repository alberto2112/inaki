"""ChannelRouterIntermediateSink — adapta un ``ChannelRouter`` + target a
``IIntermediateSink``.

Lo usa el scheduler cuando un ``AgentSendPayload`` tiene ``output_channel``:
mientras el agente ejecuta su turno, cada bloque de texto que el LLM emita
junto con tool_calls viaja en vivo al mismo canal (telegram:chat_id,
file:///path, etc.). El ``reply`` final sigue enviándose por el camino
habitual del scheduler (``_finalize_task`` → ``channel_sender.send_message``).

El sink se construye por-turno: cada tarea del scheduler que dispara un
agent_send con output_channel crea su propio sink con el target resuelto.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.ports.outbound.intermediate_sink_port import IIntermediateSink

if TYPE_CHECKING:
    from adapters.outbound.scheduler.dispatch_adapters import ChannelRouter

logger = logging.getLogger(__name__)


class ChannelRouterIntermediateSink(IIntermediateSink):
    """Reenvía cada intermedio al target vía ``ChannelRouter.send_message``."""

    def __init__(self, router: ChannelRouter, target: str) -> None:
        self._router = router
        self._target = target

    async def emit(self, text: str) -> None:
        try:
            await self._router.send_message(self._target, text)
        except Exception as exc:  # pragma: no cover — defensivo
            # No dejamos que un fallo de red/sink rompa el tool loop del agente.
            logger.warning(
                "ChannelRouterIntermediateSink: fallo enviando intermedio a '%s': %s",
                self._target,
                exc,
            )
