"""SchedulerReconciler — reconcilia las tareas builtin contra la config actual.

Extraído de ``AppContainer``: encapsula el *cómo* de la reconciliación (seed
de la fila si no existe, update de schedule, reset de estados FAILED arrastrados,
recompute de ``next_run`` siempre en la timezone del usuario vía el helper
central de cron). El *qué* — qué tareas builtin reconciliar, con qué schedule y
para qué agente — lo decide el caller, que tiene el estado de config y agentes.

El cron se evalúa SIEMPRE en ``user.timezone`` (igual que el repo y el service)
para no reintroducir el bug histórico de doble ejecución por offset DST.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.domain.entities.task import ScheduledTask, TaskStatus
from core.domain.utils.cron import next_cron_occurrence, resolve_timezone
from core.ports.outbound.scheduler_port import ISchedulerRepository

logger = logging.getLogger(__name__)


class SchedulerReconciler:
    """Garantiza que una tarea builtin en la DB refleja la config actual."""

    def __init__(self, repo: ISchedulerRepository, user_timezone: str) -> None:
        self._repo = repo
        self._user_timezone = user_timezone

    async def reconcile_builtin_task(self, target: ScheduledTask) -> None:
        """Reconcilia ``target`` contra su fila en la DB:

        - no existe → seed con schedule de config + next_run computado
        - schedule cambió en config → update + recompute next_run
        - status = FAILED (arrastre de corridas viejas rotas) → reset a pending
        - next_run NULL → recompute
        """
        await self._repo.ensure_schema()
        existing = await self._repo.get_task(target.id)

        if existing is None:
            # seed_builtin computa next_run si es recurrente y viene None
            await self._repo.seed_builtin(target)
            logger.info("Tarea builtin %s sembrada con schedule '%s'", target.name, target.schedule)
            return

        cron_tz = resolve_timezone(self._user_timezone)
        now = datetime.now(timezone.utc)
        needs_save = False
        new_schedule = existing.schedule
        new_next_run = existing.next_run
        new_status = existing.status
        new_retry = existing.retry_count

        if existing.schedule != target.schedule:
            new_schedule = target.schedule
            new_next_run = next_cron_occurrence(new_schedule, cron_tz, after=now)
            logger.info(
                "%s: schedule actualizado '%s' → '%s'",
                target.name,
                existing.schedule,
                target.schedule,
            )
            needs_save = True

        if new_status == TaskStatus.FAILED:
            new_status = TaskStatus.PENDING
            new_retry = 0
            if new_next_run is None or new_next_run <= now:
                new_next_run = next_cron_occurrence(new_schedule, cron_tz, after=now)
            logger.info(
                "%s: estado FAILED reseteado a PENDING (next_run=%s)",
                target.name,
                new_next_run,
            )
            needs_save = True

        if new_next_run is None:
            new_next_run = next_cron_occurrence(new_schedule, cron_tz, after=now)
            logger.info("%s: next_run era NULL → recomputado a %s", target.name, new_next_run)
            needs_save = True

        if needs_save:
            updated = existing.model_copy(
                update={
                    "schedule": new_schedule,
                    "next_run": new_next_run,
                    "status": new_status,
                    "retry_count": new_retry,
                }
            )
            await self._repo.save_task(updated)
