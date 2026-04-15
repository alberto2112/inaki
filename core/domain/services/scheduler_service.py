"""
SchedulerService — motor de ejecución del scheduler.

Loop principal:
  - Obtiene la próxima tarea pendiente
  - Espera hasta su next_run (máx 60s o hasta invalidación)
  - Ejecuta la tarea con reintentos
  - Finaliza y recomputa next_run para tareas recurrentes
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from croniter import croniter

from core.domain.entities.task import (
    AgentSendPayload,
    ChannelSendPayload,
    ConsolidateMemoryPayload,
    ScheduledTask,
    ShellExecPayload,
    TaskKind,
    TaskStatus,
    WebhookPayload,
)
from core.domain.entities.task_log import TaskLog
from core.domain.errors import InvalidTriggerTypeError

if TYPE_CHECKING:
    from adapters.outbound.scheduler.dispatch_adapters import SchedulerDispatchPorts
    from core.ports.outbound.scheduler_port import ISchedulerRepository
    from infrastructure.config import SchedulerConfig

logger = logging.getLogger(__name__)


class SchedulerService:

    def __init__(
        self,
        repo: ISchedulerRepository,
        dispatch: SchedulerDispatchPorts,
        config: SchedulerConfig,
    ) -> None:
        self._repo = repo
        self._dispatch = dispatch
        self._config = config
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def invalidate(self) -> None:
        """Called by use case after any mutation to wake the loop."""
        self._wake.set()

    async def start(self) -> None:
        await self._repo.ensure_schema()
        await self._handle_missed_on_startup()
        self._task = asyncio.create_task(self._loop(), name="scheduler-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            now = datetime.now(timezone.utc)
            next_task = await self._repo.get_next_due(now)
            if next_task is None:
                # No active tasks — sleep up to 60s or until invalidated
                self._wake.clear()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=60.0)
                continue
            wait_secs = (
                (next_task.next_run - now).total_seconds()
                if next_task.next_run
                else 0.0
            )
            if wait_secs > 0:
                self._wake.clear()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=min(wait_secs, 60.0))
                continue
            await self._execute_task(next_task)

    async def _run_once(self) -> None:
        """Test helper: process one due task if any, then return."""
        now = datetime.now(timezone.utc)
        due = await self._repo.list_due_pending(now)
        if due:
            await self._execute_task(due[0])

    async def _handle_missed_on_startup(self) -> None:
        now = datetime.now(timezone.utc)
        missed = await self._repo.list_due_pending(now)
        for task in missed:
            if task.task_kind == TaskKind.ONESHOT:
                await self._repo.update_status(task.id, TaskStatus.MISSED)
                await self._repo.save_log(
                    TaskLog(
                        task_id=task.id,
                        started_at=now,
                        finished_at=now,
                        status="missed",
                        error="Task was not running when scheduled time passed",
                    )
                )
            else:
                # Recurrent: recompute next_run, skip missed occurrences
                next_run = datetime.fromtimestamp(
                    croniter(task.schedule, now).get_next(), tz=timezone.utc
                )
                await self._repo.update_after_execution(
                    task.id,
                    success=True,
                    output=None,
                    next_run=next_run,
                    executions_remaining=task.executions_remaining,
                )

    async def _execute_task(self, task: ScheduledTask) -> None:
        await self._repo.update_status(task.id, TaskStatus.RUNNING)
        output: str | None = None
        error: str | None = None
        success = False
        attempt = 0
        dispatch_metadata: dict | None = None

        for attempt in range(self._config.max_retries + 1):
            started_at = datetime.now(timezone.utc)
            try:
                output, dispatch_metadata = await self._dispatch_trigger(task)
                success = True
                break
            except Exception as exc:
                error = str(exc)
                logger.warning("Task %s attempt %d failed: %s", task.id, attempt + 1, exc)
                # Persist current retry count after each failure
                await self._repo.update_status(
                    task.id, TaskStatus.RUNNING, retry_count=attempt + 1
                )

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

        if success:
            await self._finalize_task(task, output, dispatch_metadata)
        else:
            await self._repo.update_status(task.id, TaskStatus.FAILED, retry_count=attempt + 1)

    async def _finalize_task(
        self,
        task: ScheduledTask,
        output: str | None,
        dispatch_metadata: dict | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        truncated = output[: self._config.output_truncation_size] if output else None
        if task.log_enabled:
            await self._repo.save_log(
                TaskLog(
                    task_id=task.id,
                    started_at=now,
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
                next_run = datetime.fromtimestamp(
                    croniter(task.schedule, now).get_next(), tz=timezone.utc
                )
                await self._repo.update_after_execution(
                    task.id,
                    success=True,
                    output=truncated,
                    next_run=next_run,
                    executions_remaining=remaining,
                    retry_count=0,
                )

    async def _dispatch_trigger(
        self, task: ScheduledTask
    ) -> tuple[str | None, dict | None]:
        """Ejecuta el trigger y devuelve ``(output, dispatch_metadata)``.

        ``dispatch_metadata`` contiene ``{original_target, resolved_target}`` cuando
        hubo un envío por canal (directo o via ``output_channel``); ``None`` en caso
        contrario.
        """
        payload = task.trigger_payload
        if isinstance(payload, ChannelSendPayload):
            dr = await self._dispatch.channel_sender.send_message(
                payload.target, payload.text
            )
            return None, {
                "original_target": dr.original_target,
                "resolved_target": dr.resolved_target,
            }
        elif isinstance(payload, AgentSendPayload):
            result = await self._dispatch.llm_dispatcher.dispatch(
                payload.agent_id, payload.task, payload.tools_override
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
            return await self._run_shell(payload), None
        elif isinstance(payload, ConsolidateMemoryPayload):
            return await self._dispatch.consolidator.consolidate_all(), None
        elif isinstance(payload, WebhookPayload):
            return await self._dispatch.http_caller.call(payload), None
        else:
            raise InvalidTriggerTypeError(f"Unknown payload type: {type(payload)}")

    async def _run_shell(self, payload: ShellExecPayload) -> str:
        import os

        proc = await asyncio.create_subprocess_shell(
            payload.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=payload.working_dir,
            env={**os.environ, **(payload.env_vars or {})},
        )
        timeout = payload.timeout or 300
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"shell_exec exited with code {proc.returncode}")
        return stdout.decode(errors="replace")
