"""ScheduleTaskUseCase — CRUD de tareas programadas."""

from __future__ import annotations

import logging
from pathlib import Path
import json

from core.domain.entities.task import ScheduledTask, TaskStatus
from core.ports.inbound.scheduler_port import ISchedulerUseCase

logger = logging.getLogger(__name__)


class ScheduleTaskUseCase(ISchedulerUseCase):
    """
    Implementación simple de scheduler usando un fichero JSON.
    Para producción en Pi 5 se puede migrar a SQLite.
    """

    def __init__(self, tasks_file: str) -> None:
        self._tasks_file = Path(tasks_file)
        self._tasks_file.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[ScheduledTask]:
        if not self._tasks_file.exists():
            return []
        with self._tasks_file.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return [ScheduledTask(**item) for item in raw]

    def _save(self, tasks: list[ScheduledTask]) -> None:
        with self._tasks_file.open("w", encoding="utf-8") as f:
            json.dump([t.model_dump(mode="json") for t in tasks], f, indent=2, ensure_ascii=False)

    async def schedule(self, task: ScheduledTask) -> ScheduledTask:
        tasks = self._load()
        tasks = [t for t in tasks if t.id != task.id]  # upsert
        tasks.append(task)
        self._save(tasks)
        logger.info("Tarea programada: '%s' (%s)", task.name, task.id)
        return task

    async def cancel(self, task_id: str) -> None:
        tasks = self._load()
        tasks = [t for t in tasks if t.id != task_id]
        self._save(tasks)
        logger.info("Tarea cancelada: %s", task_id)

    async def list_tasks(self, agent_id: str) -> list[ScheduledTask]:
        tasks = self._load()
        return [t for t in tasks if t.agent_id == agent_id]
