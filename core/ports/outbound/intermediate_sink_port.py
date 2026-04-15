"""Puerto outbound para emitir mensajes intermedios del asistente.

Durante un turno que usa tool_calls, el LLM puede emitir texto narrativo
(p. ej. "ok, voy a buscar esto...") JUNTO con los tool_calls en la misma
respuesta. Este puerto permite al tool loop empujar esos textos al canal
inbound (CLI, Telegram, REST) antes de ejecutar las tools, para que el
usuario vea progreso en vivo.

El mensaje FINAL del turno (el que cierra sin tool_calls) NO pasa por
este sink — sigue retornándose a través de ``RunAgentUseCase.execute()``.
Esta separación permite que los inbound adapters rendericen el mensaje
final como siempre (con spinner, formateo, etc.) mientras los intermedios
son text-only informativos.

Implementaciones:
- ``NullIntermediateSink`` — descarta todo. Default para contextos no
  interactivos o cuando el inbound no soporta progreso incremental.
- Inbound-específicas (CLI, Telegram, REST): cada canal implementa su
  propia forma de entregar el texto al usuario.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IIntermediateSink(ABC):
    """Contrato para emitir texto intermedio del asistente durante un turno."""

    @abstractmethod
    async def emit(self, text: str) -> None:
        """Empuja ``text`` al canal del usuario.

        Debe ser no-bloqueante a efectos prácticos: el tool loop llama a
        este método entre iteraciones y una latencia alta retrasa la
        ejecución de las tools.
        """
        ...


class NullIntermediateSink(IIntermediateSink):
    """Sink que descarta los mensajes intermedios.

    Se usa como default para no obligar a todos los callers a pasar un
    sink cuando no les interesa (scheduler sin destino interactivo,
    tests, one-shot, etc.).
    """

    async def emit(self, text: str) -> None:  # noqa: D401 — sink no-op
        return None
