"""NullSink — descarta el mensaje silenciosamente.

Útil como destino explícito (``null:``) cuando el usuario quiere agendar
una task sin que notifique a nadie, o como valor de test.
"""

from __future__ import annotations

from core.domain.value_objects.dispatch_result import DispatchResult
from core.ports.outbound.outbound_sink_port import IOutboundSink


class NullSink(IOutboundSink):
    """Sink que ignora el mensaje. Nunca falla.

    El ``target`` se propaga tal cual a ``original_target`` y ``resolved_target``
    en el ``DispatchResult``; la distinción entre ambos valores la aplica el
    ``ChannelRouter`` cuando redirige tras una cascada.
    """

    prefix = "null"

    async def send(self, target: str, text: str) -> DispatchResult:
        return DispatchResult(original_target=target, resolved_target=target)
