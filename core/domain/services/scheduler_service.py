"""
SchedulerService — motor de ejecución del scheduler.

Loop principal:
  - Obtiene la próxima tarea pendiente
  - Espera hasta su next_run (máx 60s o hasta invalidación)
  - Ejecuta la tarea con reintentos (backoff lineal entre intentos)
  - Finaliza y recomputa next_run para tareas recurrentes

Semántica de fallos:
  - ONESHOT que agota reintentos → FAILED (terminal).
  - RECURRENT que agota reintentos → avanza al próximo slot del cron y sigue
    PENDING: el fallo de UNA ocurrencia no mata la recurrencia. Los intentos
    fallidos quedan registrados en task_logs.

Toda evaluación de cron pasa por ``core.domain.utils.cron`` con la timezone
del usuario — nunca evaluar croniter acá directamente (ver docstring de ese
módulo para la historia del bug de doble ejecución).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.domain.entities.task import (
    AgentSendPayload,
    ChannelSendPayload,
    ConsolidateMemoryPayload,
    ReconcileMemoryPayload,
    ScheduledTask,
    ShellExecPayload,
    TaskKind,
    TaskStatus,
    WebhookPayload,
)
from core.domain.entities.task_log import TaskLog
from core.domain.errors import InvalidTriggerTypeError, TaskNotFoundError
from core.domain.utils.cron import next_cron_occurrence, resolve_timezone
from core.domain.value_objects.manual_run_result import ManualRunResult
from core.ports.inbound.scheduler_port import IManualTaskRunner

if TYPE_CHECKING:
    from core.ports.outbound.scheduler_dispatch_port import SchedulerDispatchPorts
    from core.ports.outbound.scheduler_port import ISchedulerRepository

logger = logging.getLogger(__name__)


class SchedulerService(IManualTaskRunner):
    def __init__(
        self,
        repo: ISchedulerRepository,
        dispatch: SchedulerDispatchPorts,
        max_retries: int = 3,
        output_truncation_size: int = 65536,
        user_timezone: str = "UTC",
        retry_backoff_seconds: float = 10.0,
    ) -> None:
        self._repo = repo
        self._dispatch = dispatch
        # Parámetros sueltos en lugar de SchedulerConfig completo: el service
        # solo consume estos campos (el resto del bloque scheduler es
        # wiring de infrastructure — db, enabled, channel_fallback).
        self._max_retries = max(0, int(max_retries))
        self._output_truncation_size = int(output_truncation_size)
        self._cron_tz = resolve_timezone(user_timezone)
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def invalidate(self) -> None:
        """Called by use case after any mutation to wake the loop."""
        self._wake.set()

    async def start(self) -> None:
        await self._repo.ensure_schema()
        await self._recover_on_startup()
        self._task = asyncio.create_task(self._loop(), name="scheduler-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def run_task_now(self, task_id: int) -> ManualRunResult:
        """Dispara una tarea on-demand, fuera de su agenda — NO destructivo.

        Pensado para TESTEAR tareas: ejecuta el trigger UNA sola vez (sin la
        máquina de reintentos del loop) y NO toca ``status`` / ``next_run`` /
        ``executions_remaining`` — la tarea sigue su agenda intacta, sin
        consumirse (un oneshot no pasa a COMPLETED, un recurrente no decrementa).

        Si ``log_enabled``, deja rastro en ``task_logs`` con
        ``metadata={"trigger": "manual"}`` para que la corrida manual sea
        distinguible de las programadas en ``inaki scheduler logs``.

        Raises:
            TaskNotFoundError: si ``task_id`` no existe.
        """
        task = await self._repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task {task_id} not found")

        started_at = datetime.now(timezone.utc)
        output: str | None = None
        error: str | None = None
        dispatch_metadata: dict | None = None
        success = False
        try:
            # ephemeral=True: un agent_send disparado a mano NO persiste el turno
            # en el historial — testear una tarea no debe ensuciar la conversación
            # real (los demás triggers ignoran el flag).
            output, dispatch_metadata = await self._dispatch_trigger(task, ephemeral=True)
            success = True
        except Exception as exc:
            error = str(exc)
            logger.warning("Manual run de task %s falló: %s", task_id, exc)

        if task.log_enabled:
            await self._repo.save_log(
                TaskLog(
                    task_id=task.id,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    status="success" if success else "failed",
                    output=output[: self._output_truncation_size] if output else None,
                    error=error,
                    # Merge con la metadata del dispatch (target original/resuelto en
                    # channel_send) + marcador de origen manual.
                    metadata={**(dispatch_metadata or {}), "trigger": "manual"},
                )
            )

        return ManualRunResult(
            task_id=task.id,
            success=success,
            output=output,
            error=error,
        )

    async def _loop(self) -> None:
        while True:
            try:
                now = datetime.now(timezone.utc)
                next_task = await self._repo.get_next_due()
                if next_task is None or next_task.next_run is None:
                    # No active tasks — sleep up to 60s or until invalidated
                    self._wake.clear()
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=60.0)
                    continue
                wait_secs = (next_task.next_run - now).total_seconds()
                if wait_secs > 0:
                    self._wake.clear()
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=min(wait_secs, 60.0))
                    continue
                await self._execute_task(next_task)
            except asyncio.CancelledError:
                raise
            except sqlite3.ProgrammingError as exc:
                # En Python < 3.12, el shutdown del event loop interrumpe aiosqlite
                # con sqlite3.ProgrammingError antes de que llegue el CancelledError.
                # sleep(1) cede el control — si hay cancelación pendiente se dispara ahí.
                logger.debug("Error en loop del scheduler (posible shutdown): %s", exc)
                await asyncio.sleep(1)
            except Exception:
                # Errores reales (DB corrupta, bug en dispatch) deben ser visibles
                # en producción — no enterrarlos en DEBUG.
                logger.exception("Error inesperado en loop del scheduler")
                await asyncio.sleep(1)

    async def _run_once(self) -> None:
        """Test helper: process one due task if any, then return."""
        now = datetime.now(timezone.utc)
        due = await self._repo.list_due_pending(now)
        if due:
            await self._execute_task(due[0])

    async def _recover_on_startup(self) -> None:
        """Recupera estado runtime tras un arranque del daemon.

        1. Tareas atrapadas en RUNNING (el daemon murió a mitad de ejecución):
           ONESHOT → FAILED con log explicativo; RECURRENT → avanza al próximo
           slot y vuelve a PENDING.
        2. Tareas PENDING cuyo next_run quedó en el pasado:
           ONESHOT → MISSED; RECURRENT → avanza al próximo slot (las
           ocurrencias perdidas no se re-ejecutan ni tocan last_run).
        """
        now = datetime.now(timezone.utc)

        stuck = await self._repo.list_running()
        for task in stuck:
            if task.log_enabled:
                await self._repo.save_log(
                    TaskLog(
                        task_id=task.id,
                        started_at=now,
                        finished_at=now,
                        status="failed",
                        error="Daemon restarted while task was running",
                    )
                )
            if task.task_kind == TaskKind.ONESHOT:
                await self._repo.update_status(task.id, TaskStatus.FAILED)
            else:
                await self._advance_recurrent(task, now)
            logger.warning(
                "Task %s estaba RUNNING al arrancar — recuperada (%s)",
                task.id,
                task.task_kind.value,
            )

        missed = await self._repo.list_due_pending(now)
        for task in missed:
            if task.log_enabled:
                # La ocurrencia salteada deja rastro en task_logs — sin esto,
                # "¿por qué ayer no llegó el resumen de las 6?" no tiene
                # respuesta en los logs (el oneshot la tenía; la recurrente no).
                await self._repo.save_log(
                    TaskLog(
                        task_id=task.id,
                        started_at=now,
                        finished_at=now,
                        status="missed",
                        error="Task was not running when scheduled time passed",
                    )
                )
            if task.task_kind == TaskKind.ONESHOT:
                await self._repo.update_status(task.id, TaskStatus.MISSED)
            else:
                # Recurrent: recompute next_run, skip missed occurrences.
                # last_run NO se toca: la tarea no se ejecutó.
                await self._advance_recurrent(task, now)

    async def _advance_recurrent(self, task: ScheduledTask, now: datetime) -> None:
        """Avanza una recurrente al próximo slot del cron sin registrar ejecución."""
        next_run = next_cron_occurrence(task.schedule, self._cron_tz, after=now)
        await self._repo.update_after_execution(
            task.id,
            next_run=next_run,
            executions_remaining=task.executions_remaining,
            retry_count=0,
            last_run=None,
        )

    async def _execute_task(self, task: ScheduledTask) -> None:
        await self._repo.update_status(task.id, TaskStatus.RUNNING)
        output: str | None = None
        error: str | None = None
        success = False
        attempt = 0
        dispatch_metadata: dict | None = None
        run_started_at = datetime.now(timezone.utc)

        for attempt in range(self._max_retries + 1):
            started_at = datetime.now(timezone.utc)
            try:
                output, dispatch_metadata = await self._dispatch_trigger(task)
                success = True
                break
            except Exception as exc:
                error = str(exc)
                logger.warning("Task %s attempt %d failed: %s", task.id, attempt + 1, exc)
                # Persist current retry count after each failure
                await self._repo.update_status(task.id, TaskStatus.RUNNING, retry_count=attempt + 1)

            if task.log_enabled:
                await self._repo.save_log(
                    TaskLog(
                        task_id=task.id,
                        started_at=started_at,
                        finished_at=datetime.now(timezone.utc),
                        status="failed",
                        error=error,
                    )
                )
            if attempt < self._max_retries and self._retry_backoff_seconds > 0:
                # Backoff lineal: 1×, 2×, 3×... — sin esto, un agent_send
                # fallido dispara N corridas completas de LLM back-to-back.
                await asyncio.sleep(self._retry_backoff_seconds * (attempt + 1))

        if success:
            await self._finalize_task(task, output, dispatch_metadata, run_started_at)
        elif task.task_kind == TaskKind.RECURRENT:
            # El fallo de una ocurrencia no mata la recurrencia: avanzar al
            # próximo slot. Los intentos fallidos ya quedaron en task_logs.
            logger.warning(
                "Task %s falló tras %d intentos — recurrente: avanza al próximo slot",
                task.id,
                attempt + 1,
            )
            await self._advance_recurrent(task, datetime.now(timezone.utc))
        else:
            await self._repo.update_status(task.id, TaskStatus.FAILED, retry_count=attempt + 1)

    async def _finalize_task(
        self,
        task: ScheduledTask,
        output: str | None,
        dispatch_metadata: dict | None = None,
        started_at: datetime | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        truncated = output[: self._output_truncation_size] if output else None
        if task.log_enabled:
            await self._repo.save_log(
                TaskLog(
                    task_id=task.id,
                    started_at=started_at or now,
                    finished_at=now,
                    status="success",
                    output=truncated,
                    metadata=dispatch_metadata,
                )
            )
        if task.task_kind == TaskKind.ONESHOT:
            await self._repo.update_status(task.id, TaskStatus.COMPLETED)
        else:
            # Recurrent
            remaining = task.executions_remaining
            if remaining is not None:
                remaining -= 1
            if remaining == 0:
                await self._repo.update_status(task.id, TaskStatus.COMPLETED)
            else:
                next_run = next_cron_occurrence(task.schedule, self._cron_tz, after=now)
                await self._repo.update_after_execution(
                    task.id,
                    next_run=next_run,
                    executions_remaining=remaining,
                    retry_count=0,
                    last_run=now,
                )

    async def _dispatch_trigger(
        self, task: ScheduledTask, ephemeral: bool = False
    ) -> tuple[str | None, dict | None]:
        """Ejecuta el trigger y devuelve ``(output, dispatch_metadata)``.

        ``dispatch_metadata`` contiene ``{original_target, resolved_target}`` cuando
        hubo un envío por canal (directo o via ``output_channel``); ``None`` en caso
        contrario.

        ``ephemeral`` aplica a ``agent_send`` (el turno del agente NO se persiste
        en el historial) y a ``channel_send`` (el mensaje enviado NO se registra
        en el historial). Lo usa ``run_task_now`` para que un disparo manual de
        prueba no ensucie la conversación real. Los demás triggers lo ignoran.
        """
        payload = task.trigger_payload
        if isinstance(payload, ChannelSendPayload):
            dr = await self._dispatch.channel_sender.send_message(payload.target, payload.text)
            # Persistir el envío como mensaje del asistente en el historial del
            # agente DUEÑO de la conversación: ``payload.agent_id`` si quien agendó lo
            # informó explícito (un cronista que publica EN NOMBRE DE otro agente), o
            # ``task.created_by`` en su defecto (el que agendó es el dueño). Se omite en
            # pruebas manuales (ephemeral) o cuando no hay dueño alguno (CLI sin
            # agent_id). El recorder es no-op si el target resuelto no es un canal
            # conversacional vivo.
            owner = payload.agent_id or task.created_by
            if not ephemeral and owner:
                await self._dispatch.history_recorder.record_channel_send(
                    owner, dr.resolved_target, payload.text
                )
            return None, {
                "original_target": dr.original_target,
                "resolved_target": dr.resolved_target,
            }
        elif isinstance(payload, AgentSendPayload):
            # Cuando hay output_channel, armamos un sink que reenvía los
            # bloques intermedios del LLM (narración junto con tool_calls)
            # al mismo canal en VIVO — antes de ejecutar cada tool. Así el
            # destinatario ve el progreso del agente tal y como sucede,
            # no solo el reply final.
            #
            # Además, propagamos el (channel, chat_id) parseados del target
            # a execute() para que el intercambio (user prompt + assistant
            # response) se persista en el bucket del canal destino — si no,
            # quedaría aislado en el bucket default y el usuario perdería
            # el contexto al iterar sobre la respuesta.
            live_sink = None
            channel = ""
            chat_id = ""
            if payload.output_channel:
                live_sink = self._dispatch.channel_sender.build_intermediate_sink(
                    payload.output_channel
                )
                _ch, _sep, _cid = payload.output_channel.partition(":")
                if _sep:
                    channel = _ch
                    chat_id = _cid
            result = await self._dispatch.llm_dispatcher.dispatch(
                payload.agent_id,
                payload.task,
                payload.tools_override,
                intermediate_sink=live_sink,
                channel=channel,
                chat_id=chat_id,
                ephemeral=ephemeral,
            )
            if payload.output_channel:
                dr = await self._dispatch.channel_sender.send_message(
                    payload.output_channel, result
                )
                return None, {
                    "original_target": dr.original_target,
                    "resolved_target": dr.resolved_target,
                }
            return result, None
        elif isinstance(payload, ShellExecPayload):
            return await self._dispatch.shell_executor.run(payload), None
        elif isinstance(payload, ConsolidateMemoryPayload):
            return await self._dispatch.consolidator.consolidate_all(), None
        elif isinstance(payload, ReconcileMemoryPayload):
            return await self._dispatch.reconciler.reconcile(payload.agent_id), None
        elif isinstance(payload, WebhookPayload):
            return await self._dispatch.http_caller.call(payload), None
        else:
            raise InvalidTriggerTypeError(f"Unknown payload type: {type(payload)}")
