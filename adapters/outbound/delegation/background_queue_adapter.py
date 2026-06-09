"""BackgroundDelegationQueueAdapter — implementación in-memory de
``IBackgroundDelegationQueue``.

Mantiene un dict de tasks in-flight + una cola FIFO consumida por un único
``asyncio.Task`` bajo un ``asyncio.Semaphore`` que limita la concurrencia. Al
terminar cada delegación, inyecta el resultado en el ``(channel, chat_id)``
original via ``ILLMDispatcher.dispatch`` con el marker ``[bg-N] ...``
(REQ-BGD-5, REQ-DG-11). La task se purga del dict SOLO si el dispatch tuvo
éxito (con reintentos); si no se pudo entregar, queda visible en el snapshot
para no perderse en silencio.

El adapter es 100% in-memory (REQ-BGD-8): no persiste estado; en restart del
daemon las tasks in-flight se pierden.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.domain.entities.background_task import BackgroundTask, BackgroundTaskView

if TYPE_CHECKING:
    from core.ports.outbound.llm_dispatcher_port import ILLMDispatcher
    from core.use_cases.run_agent_one_shot import RunAgentOneShotUseCase

logger = logging.getLogger(__name__)

# Reintentos del dispatch del resultado al scope original. El dispatch corre un
# turno del LLM padre: los fallos suelen ser transitorios, así que reintentamos
# unas pocas veces con backoff lineal corto antes de dejar la task visible.
_DISPATCH_ATTEMPTS = 3
_DISPATCH_RETRY_DELAY = 0.5  # segundos; el delay efectivo es delay * intento


class BackgroundDelegationQueueAdapter:
    """Cola in-memory + consumer asyncio para delegaciones async."""

    def __init__(
        self,
        *,
        dispatcher: "ILLMDispatcher",
        one_shot_resolver: Callable[[str], "RunAgentOneShotUseCase | None"],
        max_iterations_per_sub: int,
        timeout_seconds: int,
        max_concurrent: int = 3,
    ) -> None:
        self._dispatcher = dispatcher
        self._one_shot_resolver = one_shot_resolver
        self._max_iter = max_iterations_per_sub
        self._timeout = timeout_seconds
        self._tasks: dict[str, BackgroundTask] = {}
        self._queue: asyncio.Queue[BackgroundTask] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._consumer_task: asyncio.Task | None = None
        self._id_counter: int = 0

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
        """Registra una nueva delegación y devuelve su ``task_id`` (REQ-BGD-2)."""
        self._id_counter += 1
        task_id = f"bg-{self._id_counter}"
        task = BackgroundTask(
            id=task_id,
            caller_agent_id=caller_agent_id,
            target_agent_id=target_agent_id,
            prompt=prompt,
            system_prompt=system_prompt,
            channel=channel,
            chat_id=chat_id,
            started_at=datetime.now(timezone.utc),
            status="queued",
        )
        self._tasks[task_id] = task
        self._queue.put_nowait(task)
        return task_id

    def snapshot_inflight(self, caller_agent_id: str) -> list[BackgroundTaskView]:
        """Devuelve tasks ``queued``/``running`` del caller, ordenadas por start time."""
        now = datetime.now(timezone.utc)
        propias = [t for t in self._tasks.values() if t.caller_agent_id == caller_agent_id]
        propias.sort(key=lambda t: t.started_at)
        return [BackgroundTaskView.from_task(t, now=now) for t in propias]

    async def start(self) -> None:
        """Lanza el consumer (REQ-BGD-1). Idempotente."""
        if self._consumer_task is not None and not self._consumer_task.done():
            return
        self._consumer_task = asyncio.create_task(
            self._loop(), name="background-delegation-consumer"
        )

    async def stop(self) -> None:
        """Cancela el consumer; las tasks in-flight se abandonan (REQ-BGD-8)."""
        if self._consumer_task is None:
            return
        self._consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._consumer_task
        self._consumer_task = None

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _loop(self) -> None:
        """Consumer principal: dispara ``_run_task`` por cada item de la cola."""
        while True:
            task = await self._queue.get()
            asyncio.create_task(self._run_task(task), name=f"bg-delegation-{task.id}")

    async def _run_task(self, task: BackgroundTask) -> None:
        """Ejecuta una delegación bajo el semáforo y dispatcha el resultado.

        Orden de purga (FIX silent-death): la task se elimina de ``_tasks``
        SOLO si el dispatch del resultado tuvo éxito. Antes se purgaba en un
        ``finally`` previo al dispatch, así que un dispatch fallido borraba la
        task del snapshot y el ``[bg-N]`` nunca llegaba — el agente padre
        quedaba esperando un resultado que jamás aparecía y no podía siquiera
        ver que la delegación seguía pendiente.

        Si tras los reintentos el dispatch no se entrega, la task queda viva en
        ``_tasks`` (visible en ``snapshot_inflight`` como ``running``): es
        engañoso respecto al estado real, pero MUY preferible a desaparecer en
        silencio — el agente al menos sabe que hay algo pendiente y no la da por
        perdida ni la relanza a ciegas.
        """
        async with self._semaphore:
            task.status = "running"
            try:
                one_shot = self._one_shot_resolver(task.target_agent_id)
                if one_shot is None:
                    content = f"[{task.id}] failed: unknown_target_agent: '{task.target_agent_id}'"
                else:
                    raw = await one_shot.execute(
                        task=task.prompt,
                        system_prompt=task.system_prompt,
                        max_iterations=self._max_iter,
                        timeout_seconds=self._timeout,
                    )
                    content = f"[{task.id}] {raw}"
            except Exception as exc:  # noqa: BLE001
                content = f"[{task.id}] failed: {type(exc).__name__}: {exc}"
                logger.warning(
                    "background-delegation %s falló: %s: %s",
                    task.id,
                    type(exc).__name__,
                    exc,
                )

            entregado = await self._dispatch_result(task, content)
            if entregado:
                self._tasks.pop(task.id, None)
            else:
                logger.error(
                    "background-delegation %s: no se pudo entregar el resultado tras "
                    "%d intentos; la task queda visible en snapshot para que el agente "
                    "no la dé por perdida",
                    task.id,
                    _DISPATCH_ATTEMPTS,
                )

    async def _dispatch_result(self, task: BackgroundTask, content: str) -> bool:
        """Inyecta ``content`` en el scope original con reintentos.

        Devuelve ``True`` si algún intento tuvo éxito, ``False`` si todos
        fallaron. El dispatch corre un turno completo del agente padre (llamada
        al LLM), así que la mayoría de los fallos son transitorios (red, timeout
        del provider) — un puñado de reintentos con backoff corto los cubre sin
        sobreingeniar.
        """
        for intento in range(1, _DISPATCH_ATTEMPTS + 1):
            try:
                await self._dispatcher.dispatch(
                    agent_id=task.caller_agent_id,
                    prompt=content,
                    channel=task.channel,
                    chat_id=task.chat_id,
                )
                return True
            except Exception as dispatch_exc:  # noqa: BLE001
                logger.warning(
                    "background-delegation %s: dispatch intento %d/%d falló: %s",
                    task.id,
                    intento,
                    _DISPATCH_ATTEMPTS,
                    dispatch_exc,
                )
                if intento < _DISPATCH_ATTEMPTS:
                    await asyncio.sleep(_DISPATCH_RETRY_DELAY * intento)
        return False
