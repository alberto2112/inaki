from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.domain.entities.task import ScheduledTask
from core.domain.entities.task_log import TaskLog
from core.domain.value_objects.manual_run_result import ManualRunResult


class IManualTaskRunner(ABC):
    """Disparo manual on-demand de una tarea, fuera de su agenda.

    Segregado de ``ISchedulerUseCase`` a propósito (ISP): el CRUD de tareas solo
    necesita el repo, mientras que correr una tarea requiere el motor de dispatch
    (canales, LLM, shell...). Lo implementa el ``SchedulerService`` (el motor de
    ejecución), no el use case de CRUD. Lo consumen la tool ``scheduler`` (op
    ``run``), el CLI (``inaki scheduler run``) y el REST admin (``POST /scheduler/run``).
    """

    @abstractmethod
    async def run_task_now(self, task_id: int) -> ManualRunResult: ...


class ISchedulerUseCase(ABC):
    @abstractmethod
    async def create_task(self, task: ScheduledTask) -> ScheduledTask: ...

    @abstractmethod
    async def get_task(self, task_id: int) -> ScheduledTask: ...

    @abstractmethod
    async def list_tasks(self) -> list[ScheduledTask]: ...

    @abstractmethod
    async def update_task(self, task_id: int, **kwargs: Any) -> ScheduledTask: ...

    @abstractmethod
    async def delete_task(self, task_id: int) -> None: ...

    @abstractmethod
    async def enable_task(self, task_id: int) -> None: ...

    @abstractmethod
    async def disable_task(self, task_id: int) -> None: ...

    @abstractmethod
    async def list_logs(
        self,
        task_id: int | None,
        limit: int = 10,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> list[TaskLog]: ...

    @abstractmethod
    async def get_log(self, log_id: int) -> TaskLog | None: ...
