"""DispatchResult — value object de trazabilidad del dispatch de canales.

Captura el par (target original, target resuelto) tras la cascada de
resolución del ChannelRouter. Se persiste en ``TaskLog.metadata`` para
auditoría y para que el LLM pueda inspeccionar a dónde fue realmente
redirigido un mensaje originalmente destinado a un canal inbound-only.
"""

from __future__ import annotations

from pydantic import BaseModel


class DispatchResult(BaseModel, frozen=True):
    """Resultado inmutable de un dispatch a un sink.

    Atributos:
        original_target: El ``target`` tal como fue capturado al agendar
            (p. ej. ``"cli:local"``). Nunca mutado.
        resolved_target: El ``target`` efectivo que recibió el mensaje tras
            la cascada de resolución (p. ej. ``"file:///tmp/inaki-schedule-output.log"``).
    """

    original_target: str
    resolved_target: str
