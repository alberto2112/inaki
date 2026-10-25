"""Port ``IDelegationTranscript`` — el rastro de lo que hace un hijo one-shot.

``RunAgentOneShotUseCase.execute(..., recorder=)`` lo cablea a los dos hooks que ya
tiene el tool loop:

- ``trace`` (espejo en MEMORIA): el loop le hace ``append`` síncrono de cada mensaje
  del hijo. No puede fallar, así que el reporte de un fallo (qué tool quedó en
  vuelo, cuál fue el último paso) NO depende de que el disco ande.
- ``record`` (DURABLE): se invoca por cada mensaje en el momento en que se genera,
  para que un SIGKILL no pierda el trabajo ya hecho (REQ-BGP-2).

El kernel declara solo lo que consume; quién persiste (SQLite, hoy) es asunto del
módulo ``agents``.
"""

from __future__ import annotations

from typing import Protocol

from inaki.shared.message import Message


class IDelegationTranscript(Protocol):
    """Transcript de una delegación en curso."""

    @property
    def trace(self) -> list[Message]:
        """Espejo en memoria del transcript, en orden. Lo appendea ``run_tool_loop``."""
        ...

    async def record(self, message: Message) -> None:
        """Persiste ``message`` de forma durable.

        Contrato: NUNCA lanza. Una escritura fallida se loguea y se descarta; el
        turno del hijo sigue (REQ-BGP-8, degradación ruidosa).
        """
        ...


class NullDelegationTranscript:
    """Recorder nulo: ``trace`` es una lista en memoria, ``record`` no hace nada.

    Es el modo degradado cuando el store durable no está disponible: el hijo corre
    igual y el reporte de fallo sigue teniendo su ``trace``.
    """

    def __init__(self) -> None:
        self._trace: list[Message] = []

    @property
    def trace(self) -> list[Message]:
        return self._trace

    async def record(self, message: Message) -> None:
        return None
