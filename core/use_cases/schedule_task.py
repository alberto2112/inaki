"""ScheduleTaskUseCase — CRUD de tareas programadas."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from core.domain.entities.task import ScheduledTask, TaskStatus
from core.domain.errors import BuiltinTaskProtectedError, TaskNotFoundError
from core.ports.inbound.scheduler_port import ISchedulerUseCase

if TYPE_CHECKING:
    from core.ports.outbound.scheduler_port import ISchedulerRepository

logger = logging.getLogger(__name__)


class ScheduleTaskUseCase(ISchedulerUseCase):

    def __init__(
        self,
        repo: ISchedulerRepository,
        on_mutation: Callable[[], None],
    ) -> None:
        self._repo = repo
        self._on_mutation = on_mutation

    async def create_task(self, task: ScheduledTask) -> ScheduledTask:
        created = await self._repo.save_task(task)
        self._on_mutation()
        return created

    async def delete_task(self, task_id: int) -> None:
        await self.get_task(task_id)
        if task_id < 100:
            raise BuiltinTaskProtectedError(
                f"Task {task_id} is a builtin and cannot be deleted."
            )
        await self._repo.delete_task(task_id)
        self._on_mutation()

    async def enable_task(self, task_id: int) -> None:
        await self.get_task(task_id)
        await self._repo.update_status(task_id, TaskStatus.PENDING)
        self._on_mutation()

    async def disable_task(self, task_id: int) -> None:
        await self.get_task(task_id)
        await self._repo.update_status(task_id, TaskStatus.DISABLED)
        self._on_mutation()

    async def get_task(self, task_id: int) -> ScheduledTask:
        task = await self._repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task {task_id} not found")
        return task

    async def list_tasks(self) -> list[ScheduledTask]:
        return await self._repo.list_tasks()

    async def update_task(self, task_id: int, **kwargs: Any) -> ScheduledTask:
        if task_id < 100:
            raise BuiltinTaskProtectedError(
                f"Task {task_id} is a builtin and cannot be modified via update_task."
            )
        task = await self.get_task(task_id)
        updated = task.model_copy(update=kwargs)
        return await self._repo.save_task(updated)
