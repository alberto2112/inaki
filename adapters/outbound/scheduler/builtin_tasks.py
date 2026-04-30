from __future__ import annotations

from core.domain.entities.task import (
    AgentSendPayload,
    ConsolidateMemoryPayload,
    ScheduledTask,
    TaskKind,
    TriggerType,
)

CONSOLIDATE_MEMORY_TASK_ID = 1
FACE_DEDUP_TASK_ID = 2


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


def build_face_dedup_task(schedule: str, agent_id: str) -> ScheduledTask:
    """Construye la definición de la tarea builtin `face_dedup_nightly`.

    El cron y el agent_id vienen de `global_config.photos.dedup`. El reconciliador
    en AppContainer decide si hay que sembrar, actualizar o resetear la fila.
    """
    return ScheduledTask(
        id=FACE_DEDUP_TASK_ID,
        name="face_dedup_nightly",
        description="Deduplicación nocturna de personas en el registro facial",
        task_kind=TaskKind.RECURRENT,
        trigger_type=TriggerType.AGENT_SEND,
        trigger_payload=AgentSendPayload(
            agent_id=agent_id,
            task=(
                "Ejecutá la herramienta find_duplicate_persons y reportá los pares "
                "de personas duplicadas que encontrés, si hay alguno."
            ),
        ),
        schedule=schedule,
        executions_remaining=None,
    )
