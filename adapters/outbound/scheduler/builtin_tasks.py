from __future__ import annotations

from core.domain.entities.task import (
    ConsolidateMemoryPayload,
    ScheduledTask,
    TaskKind,
    TriggerType,
)

CONSOLIDATE_MEMORY_TASK_ID = 1


def build_consolidate_memory_task(schedule: str) -> ScheduledTask:
    """
    Construye la definición de la tarea builtin `consolidate_memory`.

    El cron viene de `global_config.memory.schedule`. Se instancia en cada
    arranque y pasa por el reconciliador de `AppContainer` que decide si
    hay que sembrar, actualizar o resetear la fila existente.
    """
    return ScheduledTask(
        id=CONSOLIDATE_MEMORY_TASK_ID,
        name="consolidate_memory",
        description="Consolidación global de memoria (todos los agentes habilitados)",
        task_kind=TaskKind.RECURRENT,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule=schedule,
        executions_remaining=None,
    )
