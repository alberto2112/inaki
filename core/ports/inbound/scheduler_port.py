from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.domain.entities.task import ScheduledTask


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
