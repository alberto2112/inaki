"""BufferingIntermediateSink — acumula los mensajes intermedios en memoria.

Pensado para canales request/response (CLI → daemon REST) donde no se puede
empujar nada al usuario hasta que el turno termina. El inbound crea el sink,
lo pasa a ``run_agent.execute(..., intermediate_sink=sink)`` y al final
lee ``sink.messages`` para devolverlos junto con la respuesta final.
"""

from __future__ import annotations

from core.ports.outbound.intermediate_sink_port import IIntermediateSink


class BufferingIntermediateSink(IIntermediateSink):
    """Sink que guarda los textos en una lista en orden de emisión."""

    def __init__(self) -> None:
        self._messages: list[str] = []

    async def emit(self, text: str) -> None:
        self._messages.append(text)

    @property
    def messages(self) -> list[str]:
        """Copia inmutable de los mensajes acumulados."""
        return list(self._messages)
