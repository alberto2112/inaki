"""ScheduleTaskUseCase — CRUD de tareas programadas."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from core.domain.entities.task import ScheduledTask, TaskStatus
from core.domain.errors import BuiltinTaskProtectedError, TaskNotFoundError, TooManyActiveTasksError
from core.ports.inbound.scheduler_port import ISchedulerUseCase

if TYPE_CHECKING:
    from core.ports.outbound.scheduler_port import ISchedulerRepository

logger = logging.getLogger(__name__)


# Campos cuya edición invalida el estado runtime (cuándo/qué/cómo ejecuta).
# Cambiar cualquiera de ellos implica resetear status/retry_count/next_run para
# que el scheduler vuelva a considerar la tarea con la definición nueva.
_INVALIDATING_FIELDS = frozenset(
    {"schedule", "trigger_payload", "task_kind", "trigger_type", "executions_remaining"}
)


class ScheduleTaskUseCase(ISchedulerUseCase):

    def __init__(
        self,
        repo: ISchedulerRepository,
        on_mutation: Callable[[], None],
    ) -> None:
        self._repo = repo
        self._on_mutation = on_mutation

    async def create_task(self, task: ScheduledTask) -> ScheduledTask:
        if task.created_by != "":
            count = await self._repo.count_active_by_agent(task.created_by)
            if count >= 21:
                raise TooManyActiveTasksError(agent_id=task.created_by)
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

        # Si la edición toca un campo invalidante, el estado runtime queda
        # stale respecto de la definición nueva. Reseteamos:
        #   - status  → pending (excepto si la task estaba disabled: respetamos
        #                        la intención explícita del usuario)
        #   - retry_count → 0 (borrón y cuenta nueva)
        #   - next_run → None (el repo lo recomputa vía _resolve_next_run con
        #                      el schedule nuevo)
        # setdefault preserva overrides explícitos del caller (ej: el LLM
        # podría forzar status=disabled aun cuando edita schedule).
        if _INVALIDATING_FIELDS.intersection(kwargs):
            if task.status != TaskStatus.DISABLED:
                kwargs.setdefault("status", TaskStatus.PENDING)
            kwargs.setdefault("retry_count", 0)
            kwargs.setdefault("next_run", None)

        updated = task.model_copy(update=kwargs)
        return await self._repo.save_task(updated)
