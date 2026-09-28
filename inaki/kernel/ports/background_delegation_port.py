"""Port `IBackgroundDelegationQueue` — cola in-memory de delegaciones async.

El feature ``background-delegation`` (REQ-BGD-1..8) desacopla las delegaciones
largas (``delegate(..., wait=False)``) del turno del agente padre. El port
expone cuatro operaciones:

- ``enqueue`` — registra una task y devuelve un ``task_id`` (``bg-N``) en <50ms,
  sin esperar a que el hijo termine (REQ-BGD-2).
- ``snapshot_inflight`` — devuelve las tasks vivas del caller (incluidas las
  que no se pudieron entregar), para el system prompt del próximo turno
  (REQ-BGD-4, 7).
- ``cancel`` — corta una task en cola o corriendo, o descarta una cuya entrega
  falló devolviendo su resultado.
- ``start`` / ``stop`` — ciclo de vida del consumer interno (REQ-BGD-1).

La implementación viene en ``adapters/outbound/delegation/`` y es 100% in-memory
(REQ-BGD-8): si el daemon reinicia, las tasks in-flight se pierden.
"""

from __future__ import annotations

from typing import Protocol

from inaki.kernel.domain.background_task import BackgroundTaskView, CancelOutcome


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
        max_iterations: int,
        timeout_seconds: int,
    ) -> str:
        """Encola una delegación. Retorna el ``task_id`` (``bg-N``) en <50ms.

        ``max_iterations`` y ``timeout_seconds`` son el presupuesto del CALLER:
        viajan con cada task porque la cola es una sola para todo el arnés y
        cada agente que delega fija el suyo.
        """
        ...

    def snapshot_inflight(self, caller_agent_id: str) -> list[BackgroundTaskView]:
        """Devuelve las tasks vivas del caller, ordenadas por start time.

        Incluye ``delivery_failed``: una task cuya entrega falló sigue siendo
        asunto pendiente del caller. Tasks entregadas o canceladas ya fueron
        purgadas (REQ-BGD-4). Lista vacía si no hay ninguna para ese caller.
        """
        ...

    def cancel(self, task_id: str, caller_agent_id: str) -> CancelOutcome:
        """Cancela ``task_id`` si pertenece a ``caller_agent_id``.

        ``queued``/``running`` → se corta (``cancelled``); ``delivery_failed`` →
        se descarta devolviendo el resultado guardado (``dismissed``);
        ``delivering`` → no se toca (cortaría el turno del padre a mitad). Una
        task de OTRO caller responde ``not_found``: un agente no ve ni corta las
        delegaciones de otro.
        """
        ...

    async def start(self) -> None:
        """Lanza el consumer task. Idempotente: segunda llamada es no-op."""
        ...

    async def stop(self) -> None:
        """Cancela el consumer y abandona in-flight sin dispatchear (REQ-BGD-8)."""
        ...
