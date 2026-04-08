from abc import ABC, abstractmethod
from core.domain.entities.task import ScheduledTask


class ISchedulerUseCase(ABC):

    @abstractmethod
    async def schedule(self, task: ScheduledTask) -> ScheduledTask: ...

    @abstractmethod
    async def cancel(self, task_id: str) -> None: ...

    @abstractmethod
    async def list_tasks(self, agent_id: str) -> list[ScheduledTask]: ...
