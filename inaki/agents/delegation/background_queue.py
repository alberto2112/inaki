"""BackgroundDelegationQueueAdapter — implementación in-memory de
``IBackgroundDelegationQueue``.

Mantiene un dict de tasks in-flight + una cola FIFO consumida por un único
``asyncio.Task`` bajo un ``asyncio.Semaphore`` que limita la concurrencia. Al
terminar cada delegación, inyecta el resultado en el ``(channel, chat_id)``
original via ``ILLMDispatcher.dispatch`` con el marker ``[bg-N] ...``
(REQ-BGD-5, REQ-DG-11). La task se purga del dict SOLO si el dispatch tuvo
éxito (con reintentos); si no se pudo entregar, queda en ``delivery_failed``
con el resultado y el error guardados, visible en el snapshot, hasta que alguien
la cancela (``cancel`` la descarta devolviendo ese resultado).

FIX bg-stuck-task: antes una entrega fallida dejaba la task en ``running`` para
siempre — el hijo había terminado pero el padre la veía "trabajando" horas
después, sin forma de cerrarla, y el resultado vivía solo en una variable local
que se perdía. Ahora el estado es honesto, el resultado sobrevive y cada task
guarda su ``asyncio.Task`` para poder cortarla.

FIX bg-result-delivery: el dispatch corre un turno completo del agente padre,
pero su valor de retorno (la respuesta digerida del padre) se DESCARTABA — se
persistía en el historial y jamás llegaba al canal, así que el usuario esperaba
un anuncio que nunca veía. Ahora, cuando el scope original es un canal
conversacional vivo (lo decide el router: ``is_conversational``), la respuesta se entrega vía
``result_sender`` al mismo ``channel:chat_id``, la narración intermedia fluye
en vivo por un ``IIntermediateSink`` del sender (mismo patrón que ``agent_send``
en el scheduler), y el turno recibe ``skip_marker`` para que el LLM pueda optar
por silencio deliberado respondiendo ``__SKIP__``.

El adapter es 100% in-memory (REQ-BGD-8): no persiste estado; en restart del
daemon las tasks in-flight se pierden.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from inaki.kernel.domain.background_task import (
    BackgroundTask,
    BackgroundTaskView,
    CancelOutcome,
)
from inaki.shared.skip_marker import SKIP_MARKER, is_skip_response

if TYPE_CHECKING:
    from inaki.kernel.ports.llm_dispatcher_port import ILLMDispatcher
    from inaki.kernel.ports.channel_port import IChannelSender
    from inaki.kernel.run_agent_one_shot import RunAgentOneShotUseCase

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
        one_shot_resolver: Callable[[str, str], "RunAgentOneShotUseCase | None"],
        max_concurrent: int = 3,
        result_sender: "IChannelSender | None" = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._one_shot_resolver = one_shot_resolver
        # Sin sender no hay entrega al canal (modo headless: tests / instancias
        # sin canales conversacionales). Producción siempre inyecta el router.
        self._result_sender = result_sender
        self._tasks: dict[str, BackgroundTask] = {}
        # asyncio.Task de cada delegación lanzada por el consumer: es lo que
        # ``cancel`` corta. Se limpia sola al terminar (done callback).
        self._handles: dict[str, asyncio.Task] = {}
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
        max_iterations: int,
        timeout_seconds: int,
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
            max_iterations=max_iterations,
            timeout_seconds=timeout_seconds,
            started_at=datetime.now(timezone.utc),
            status="queued",
        )
        self._tasks[task_id] = task
        self._queue.put_nowait(task)
        return task_id

    def snapshot_inflight(self, caller_agent_id: str) -> list[BackgroundTaskView]:
        """Devuelve las tasks vivas del caller (incluye ``delivery_failed``),
        ordenadas por start time."""
        now = datetime.now(timezone.utc)
        propias = [t for t in self._tasks.values() if t.caller_agent_id == caller_agent_id]
        propias.sort(key=lambda t: t.started_at)
        return [BackgroundTaskView.from_task(t, now=now) for t in propias]

    def cancel(self, task_id: str, caller_agent_id: str) -> CancelOutcome:
        """Corta o descarta una task del caller (ver ``IBackgroundDelegationQueue``).

        Síncrono a propósito: sacar la task del dict y pedir la cancelación del
        handle no espera a nada, y sin ``await`` de por medio no hay ventana para
        que el consumer la cambie de estado entre el chequeo y la acción.
        """
        task = self._tasks.get(task_id)
        if task is None or task.caller_agent_id != caller_agent_id:
            return CancelOutcome(outcome="not_found", task_id=task_id)
        if task.status == "delivering":
            return CancelOutcome(outcome="delivering", task_id=task_id)
        self._tasks.pop(task_id)
        if task.status == "delivery_failed":
            logger.info("background-delegation %s: descartada tras entrega fallida", task_id)
            return CancelOutcome(
                outcome="dismissed", task_id=task_id, result=task.result, error=task.error
            )
        # queued/running: si el consumer ya la lanzó, se corta su asyncio.Task;
        # si todavía está en la cola, ``_run_task`` la saltea al no encontrarla
        # en ``_tasks``.
        handle = self._handles.pop(task_id, None)
        if handle is not None:
            handle.cancel()
        logger.info("background-delegation %s: cancelada (estaba %s)", task_id, task.status)
        return CancelOutcome(outcome="cancelled", task_id=task_id)

    async def start(self) -> None:
        """Lanza el consumer (REQ-BGD-1). Idempotente."""
        if self._consumer_task is not None and not self._consumer_task.done():
            return
        self._consumer_task = asyncio.create_task(
            self._loop(), name="background-delegation-consumer"
        )

    async def stop(self) -> None:
        """Cancela el consumer y las delegaciones lanzadas; se abandonan sin
        dispatchar (REQ-BGD-8)."""
        if self._consumer_task is None:
            return
        self._consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._consumer_task
        self._consumer_task = None
        for handle in list(self._handles.values()):
            handle.cancel()

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _loop(self) -> None:
        """Consumer principal: dispara ``_run_task`` por cada item de la cola."""
        while True:
            task = await self._queue.get()
            if task.id not in self._tasks:
                continue  # cancelada mientras esperaba en la cola
            handle = asyncio.create_task(self._run_task(task), name=f"bg-delegation-{task.id}")
            self._handles[task.id] = handle
            handle.add_done_callback(partial(self._olvidar_handle, task.id))

    def _olvidar_handle(self, task_id: str, _handle: asyncio.Task) -> None:
        self._handles.pop(task_id, None)

    async def _run_task(self, task: BackgroundTask) -> None:
        """Ejecuta una delegación bajo el semáforo y dispatcha el resultado.

        Orden de purga (FIX silent-death): la task se elimina de ``_tasks``
        SOLO si el dispatch del resultado tuvo éxito. Antes se purgaba en un
        ``finally`` previo al dispatch, así que un dispatch fallido borraba la
        task del snapshot y el ``[bg-N]`` nunca llegaba — el agente padre
        quedaba esperando un resultado que jamás aparecía y no podía siquiera
        ver que la delegación seguía pendiente.

        Si tras los reintentos el dispatch no se entrega, la task queda viva en
        ``_tasks`` como ``delivery_failed``, con el resultado del hijo y el
        error guardados: el agente ve que falló la ENTREGA (no que el hijo siga
        trabajando) y ``cancel`` le devuelve el resultado.

        Una cancelación (``cancel``) llega como ``CancelledError`` en cualquier
        ``await`` de la fase del hijo; ``except Exception`` no la atrapa, así que
        se propaga limpia y el ``async with`` libera el slot del semáforo.
        """
        async with self._semaphore:
            if task.id not in self._tasks:
                return  # cancelada mientras esperaba el semáforo
            task.status = "running"
            try:
                # Resolver = construir la instancia EFÍMERA del hijo contra el CALLER
                # (hereda su config vía inherit). Por eso recibe ambos ids: el caller
                # define la herencia, el target la definición del sub-agente.
                one_shot = self._one_shot_resolver(task.caller_agent_id, task.target_agent_id)
                if one_shot is None:
                    content = f"[{task.id}] failed: unknown_target_agent: '{task.target_agent_id}'"
                else:
                    raw = await one_shot.execute(
                        task=task.prompt,
                        system_prompt=task.system_prompt,
                        max_iterations=task.max_iterations,
                        timeout_seconds=task.timeout_seconds,
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

            # Sin await entre el fin del hijo y este cambio: ``cancel`` ve
            # ``running`` o ``delivering``, nunca un estado intermedio.
            task.status = "delivering"
            ultimo_error = await self._dispatch_result(task, content)
            if ultimo_error is None:
                self._tasks.pop(task.id, None)
                return
            task.status = "delivery_failed"
            task.result = content
            task.error = f"{type(ultimo_error).__name__}: {ultimo_error}"
            logger.error(
                "background-delegation %s: no se pudo entregar el resultado tras %d "
                "intentos (último error: %s); la task queda en delivery_failed con el "
                "resultado guardado hasta que se cancele",
                task.id,
                _DISPATCH_ATTEMPTS,
                task.error,
                exc_info=ultimo_error,
            )

    async def _dispatch_result(self, task: BackgroundTask, content: str) -> Exception | None:
        """Inyecta ``content`` en el scope original con reintentos y entrega la
        respuesta del padre al canal (FIX bg-result-delivery).

        Devuelve ``None`` si algún intento tuvo éxito, o la excepción del último
        intento si todos fallaron. El dispatch corre un turno completo del agente padre (llamada
        al LLM), así que la mayoría de los fallos son transitorios (red, timeout
        del provider) — un puñado de reintentos con backoff corto los cubre sin
        sobreingeniar.

        El ``skip_marker`` le permite al padre optar por silencio deliberado
        (``__SKIP__``): en ese caso ``execute()`` ya descartó la persistencia y
        acá no se envía nada. El ``live_sink`` propaga la narración intermedia
        del turno (texto junto a tool_calls) al canal en vivo, igual que hace el
        scheduler en ``agent_send``.
        """
        target = self._conversational_target(task)
        live_sink = (
            self._result_sender.build_intermediate_sink(target, agent_id=task.caller_agent_id)
            if self._result_sender is not None and target is not None
            else None
        )
        ultimo_error: Exception | None = None
        for intento in range(1, _DISPATCH_ATTEMPTS + 1):
            try:
                response = await self._dispatcher.dispatch(
                    agent_id=task.caller_agent_id,
                    prompt=content,
                    intermediate_sink=live_sink,
                    channel=task.channel,
                    chat_id=task.chat_id,
                    skip_marker=SKIP_MARKER,
                )
            except Exception as dispatch_exc:  # noqa: BLE001
                ultimo_error = dispatch_exc
                logger.warning(
                    "background-delegation %s: dispatch intento %d/%d falló: %s: %s",
                    task.id,
                    intento,
                    _DISPATCH_ATTEMPTS,
                    type(dispatch_exc).__name__,
                    dispatch_exc,
                )
                if intento < _DISPATCH_ATTEMPTS:
                    await asyncio.sleep(_DISPATCH_RETRY_DELAY * intento)
                continue
            await self._deliver_response(task, target, response)
            return None
        return ultimo_error

    def _conversational_target(self, task: BackgroundTask) -> str | None:
        """Target ``channel:chat_id`` si el scope original es un canal
        conversacional vivo; ``None`` si no (CLI/REST sin canal, tests)."""
        if (
            self._result_sender is not None
            and task.chat_id
            and self._result_sender.is_conversational(task.channel, task.caller_agent_id)
        ):
            return f"{task.channel}:{task.chat_id}"
        return None

    async def _deliver_response(
        self, task: BackgroundTask, target: str | None, response: str
    ) -> None:
        """Entrega la respuesta del padre al canal original, best-effort.

        Un fallo acá NO marca el dispatch como fallido: reintentar re-correría
        el turno completo del LLM y duplicaría el historial. La respuesta ya
        está persistida — se loguea el error y el usuario la tiene en el
        historial aunque no le haya llegado el mensaje.
        """
        if self._result_sender is None or target is None:
            return
        if not response.strip() or is_skip_response(response):
            logger.info(
                "background-delegation %s: el agente optó por silencio — no se envía nada a %s",
                task.id,
                target,
            )
            return
        try:
            # record_history=False: el turno del dispatch ya persistió la respuesta.
            await self._result_sender.send_message(
                target, response, agent_id=task.caller_agent_id, record_history=False
            )
        except Exception as send_exc:  # noqa: BLE001
            logger.error(
                "background-delegation %s: la respuesta quedó en el historial pero "
                "no pudo entregarse a %s: %s",
                task.id,
                target,
                send_exc,
            )
