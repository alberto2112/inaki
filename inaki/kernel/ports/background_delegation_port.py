"""Port `IBackgroundDelegationQueue` — cola de delegaciones async.

El feature ``background-delegation`` (REQ-BGD-1..8) desacopla las delegaciones
largas (``delegate(..., wait=False)``) del turno del agente padre. El port
expone seis operaciones:

- ``enqueue`` — registra una task y devuelve un ``task_id`` (``bg-N``) en <50ms,
  sin esperar a que el hijo termine (REQ-BGD-2).
- ``snapshot_inflight`` — devuelve las tasks vivas del caller (incluidas las
  que no se pudieron entregar), para el system prompt del próximo turno
  (REQ-BGD-4, 7).
- ``cancel`` — corta una task en cola o corriendo, o descarta una cuya entrega
  falló devolviendo su resultado.
- ``inspect`` — transcript y desenlace de una task, viva o terminada (REQ-BGI-1).
- ``resume`` — reanuda una task que pausó con una pregunta (``awaiting_input``) sobre la
  MISMA fila: mismo id, transcript y workdir (REQ-RES-5).
- ``start`` / ``stop`` — ciclo de vida del consumer interno (REQ-BGD-1).

El estado de las tasks y su transcript sobreviven a un restart (REQ-BGD-8, REQ-BGP-1..4):
una task cortada por un restart pasa a ``interrupted`` y se le avisa al padre, no se
pierde en silencio. La implementación vive en el módulo ``agents``
(``inaki/agents/delegation/``).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Protocol

from inaki.kernel.domain.background_task import (
    BackgroundTaskView,
    CancelOutcome,
    EnqueuedTask,
    ResumeOutcome,
    TaskInspection,
)


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
        input_files: Sequence[str] = (),
    ) -> EnqueuedTask:
        """Encola una delegación. Retorna el ``task_id`` (``bg-N``) en <50ms.

        ``max_iterations`` y ``timeout_seconds`` son el presupuesto del CALLER:
        viajan con cada task porque la cola es una sola para todo el arnés y
        cada agente que delega fija el suyo.

        Si el que delega tiene un directorio de trabajo por delegación
        (``delegation.grant_workdir``), la cola lo crea ANTES de devolver y lo informa en
        ``EnqueuedTask.workdir``; si no pudo crearlo, la delegación se encola igual y lo
        dice (``workdir_unavailable``).

        ``input_files`` son rutas del workspace del caller que se COPIAN al workdir antes
        de encolar, para que el hijo las vea desde su primer paso. Si no se pueden copiar
        (flag apagado, fuera del workspace, inexistentes, topes...) lanza
        ``InputFilesRejected`` y NO se creó ningún directorio ni se encoló nada.
        """
        ...

    def snapshot_inflight(self, caller_agent_id: str) -> list[BackgroundTaskView]:
        """Devuelve las tasks pendientes del caller, ordenadas por start time.

        Incluye ``delivery_failed`` (una entrega fallida sigue siendo asunto
        pendiente del caller), ``interrupted`` mientras espera su aviso y
        ``awaiting_input`` (el hijo pausó con una pregunta que nadie contestó
        todavía). Las tasks terminales NO aparecen (REQ-BGD-4): se consultan con
        ``inspect``.
        Lista vacía si no hay ninguna para ese caller.
        """
        ...

    async def cancel(self, task_id: str, caller_agent_id: str) -> CancelOutcome:
        """Cancela ``task_id`` si pertenece a ``caller_agent_id``.

        ``queued``/``running`` → se corta (``cancelled``); ``delivery_failed`` →
        se descarta devolviendo el resultado guardado (``dismissed``);
        ``awaiting_input`` → se abandona (``dismissed``) devolviendo la pregunta sin
        contestar, sin disparar ningún dispatch; ``delivering`` → no se toca (cortaría
        el turno del padre a mitad); una task ya terminada responde ``finished``. Una
        task de OTRO caller responde ``not_found``: un agente no ve ni corta las
        delegaciones de otro.

        Es ``async`` porque persiste la transición, pero la decisión y la mutación
        del estado en memoria ocurren ANTES del primer ``await``: sin ventana para
        que el consumer cambie la task entre el chequeo y la acción.
        """
        ...

    async def resume(
        self,
        task_id: str,
        caller_agent_id: str,
        answer: str,
        *,
        target_agent_id: str | None = None,
        allowed_targets: Collection[str] = (),
    ) -> ResumeOutcome:
        """Reanuda ``task_id`` (en ``awaiting_input``) con ``answer`` como siguiente turno.

        Reabre la MISMA fila: mismo id, transcript y workdir; vuelve a ``queued`` y el
        resultado del nuevo tramo llega como un ``[bg-N] ...`` más. ``target_agent_id``,
        si se da, debe ser el de la fila (si no, ``target_mismatch``). ``allowed_targets``
        es la allow-list ``tools.allowed`` del caller (vacía = sin restricción) y se
        valida contra el target de la fila.

        Siempre devuelve un ``ResumeOutcome``; solo ``resumed`` cambia el estado de la
        task (la única otra mutación es la marca ``transcript_incomplete`` cuando la
        reapertura detecta un historial inconsistente). Una task de OTRO caller responde
        ``not_found``, igual que una podada o inexistente.

        La decisión y la reserva ocurren de forma síncrona sobre el estado en memoria;
        tras cualquier ``await`` (la carga del transcript) se vuelve a chequear.
        """
        ...

    async def inspect(
        self, task_id: str, caller_agent_id: str, *, last_n: int
    ) -> TaskInspection | None:
        """Estado, desenlace y últimos ``last_n`` pasos del transcript de ``task_id``.

        ``None`` significa "no la tengo": no existe, la podó la retención, o es de
        OTRO caller (idéntico a inexistente: no se revela su existencia). Es de solo
        lectura — no cambia el estado de la task ni dispara ningún dispatch.
        """
        ...

    async def start(self) -> None:
        """Lanza el consumer task. Idempotente: segunda llamada es no-op."""
        ...

    async def stop(self) -> None:
        """Cancela el consumer y las delegaciones en curso sin dispatchear (REQ-BGD-8).

        Las que estaban ``queued``/``running`` quedan ``interrupted``, no perdidas.
        """
        ...
