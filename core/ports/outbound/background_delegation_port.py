"""Port `IBackgroundDelegationQueue` — cola in-memory de delegaciones async.

El feature ``background-delegation`` (REQ-BGD-1..8) desacopla las delegaciones
largas (``delegate(..., wait=False)``) del turno del agente padre. El port
expone cuatro operaciones:

- ``enqueue`` — registra una task y devuelve un ``task_id`` (``bg-N``) en <50ms,
  sin esperar a que el hijo termine (REQ-BGD-2).
- ``snapshot_inflight`` — devuelve la lista de tasks ``queued``/``running`` del
  caller, para inyectar en el system prompt del próximo turno (REQ-BGD-4, 7).
- ``start`` / ``stop`` — ciclo de vida del consumer interno (REQ-BGD-1).

La implementación viene en ``adapters/outbound/delegation/`` y es 100% in-memory
(REQ-BGD-8): si el daemon reinicia, las tasks in-flight se pierden.
"""

from __future__ import annotations

from typing import Protocol

from core.domain.entities.background_task import BackgroundTaskView


class IBackgroundDelegationQueue(Protocol):
    """Cola de delegaciones async ejecutadas en background bajo un semáforo.

    Cada task se ejecuta como ``RunAgentOneShotUseCase.execute(...)``. Al
    terminar, el adapter inyecta el resultado en el ``(channel, chat_id)``
    original via ``ILLMDispatcher.dispatch(...)`` con el marker ``[bg-N] ...``
    (REQ-BGD-5, REQ-DG-11).
    """

    async def enqueue(
        self,
        *,
        caller_agent_id: str,
        target_agent_id: str,
        prompt: str,
        system_prompt: str | None,
        channel: str,
        chat_id: str,
    ) -> str:
        """Encola una delegación. Retorna el ``task_id`` (``bg-N``) en <50ms."""
        ...

    def snapshot_inflight(self, caller_agent_id: str) -> list[BackgroundTaskView]:
        """Devuelve las tasks ``queued``/``running`` del caller, ordenadas por start time.

        Tasks completadas ya fueron purgadas (REQ-BGD-4). Lista vacía si no hay
        in-flight para ese caller.
        """
        ...

    async def start(self) -> None:
        """Lanza el consumer task. Idempotente: segunda llamada es no-op."""
        ...

    async def stop(self) -> None:
        """Cancela el consumer y abandona in-flight sin dispatchear (REQ-BGD-8)."""
        ...
