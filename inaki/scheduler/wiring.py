"""Wiring del módulo scheduler (tier harness-global): config → repo, use case, service, tool.

Único fichero del módulo con permiso para importar ``inaki.config``. El
scheduler no conoce a los agentes: recibe por ``SchedulerDispatchPorts`` el
dispatcher de turnos, el consolidador y los reconciliadores, y para reconciliar
sus tareas builtin recibe la LISTA de qué reconciliar, no el registry.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from inaki.config import GlobalConfig
from inaki.kernel.ports.channel_port import IChannelSender
from inaki.kernel.ports.llm_dispatcher_port import ILLMDispatcher
from inaki.scheduler.adapters.builtin_tasks import (
    _RECONCILE_MEMORY_BASE_ID,
    build_consolidate_memory_task,
    build_face_dedup_task,
    build_reconcile_memory_task,
)
from inaki.scheduler.adapters.dispatch import (
    ConsolidationDispatchAdapter,
    HttpCallerAdapter,
    ReconcileDispatchAdapter,
    ShellExecAdapter,
    _Ejecutable,
)
from inaki.scheduler.adapters.sqlite_repo import SQLiteSchedulerRepo
from inaki.scheduler.ports.dispatch import SchedulerDispatchPorts
from inaki.scheduler.ports.use_case import IManualTaskRunner
from inaki.scheduler.reconciler import SchedulerReconciler
from inaki.scheduler.service import SchedulerService
from inaki.scheduler.tools.scheduler_tool import SchedulerTool
from inaki.scheduler.use_cases.schedule_task import ScheduleTaskUseCase
from inaki.shared.channel_context import ChannelContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchedulerBundle:
    repo: SQLiteSchedulerRepo
    use_case: ScheduleTaskUseCase
    service: SchedulerService
    reconciler: SchedulerReconciler


def build_dispatch_ports(
    *,
    channel_sender: IChannelSender,
    llm_dispatcher: ILLMDispatcher,
    consolidate_all: _Ejecutable,
    reconcilers: Mapping[str, _Ejecutable],
) -> SchedulerDispatchPorts:
    """Los puertos de despacho. El router y el dispatcher se COMPARTEN con la cola
    de delegación en background: el mismo dict de locks por scope garantiza que
    un ``bg-N`` y un trigger programado sobre la misma conversación se serialicen."""
    return SchedulerDispatchPorts(
        channel_sender=channel_sender,
        llm_dispatcher=llm_dispatcher,
        consolidator=ConsolidationDispatchAdapter(consolidate_all),
        reconciler=ReconcileDispatchAdapter(reconcilers),
        http_caller=HttpCallerAdapter(),
        shell_executor=ShellExecAdapter(),
    )


def build_scheduler(
    global_cfg: GlobalConfig, *, dispatch: SchedulerDispatchPorts
) -> SchedulerBundle:
    scheduler_cfg = global_cfg.scheduler
    tz = global_cfg.user.timezone
    repo = SQLiteSchedulerRepo(scheduler_cfg.db_filename, user_timezone=tz)
    service = SchedulerService(
        repo=repo,
        dispatch=dispatch,
        max_retries=scheduler_cfg.max_retries,
        output_truncation_size=scheduler_cfg.output_truncation_size,
        user_timezone=tz,
        retry_backoff_seconds=scheduler_cfg.retry_backoff_seconds,
    )
    # Toda mutación del CRUD invalida la vista del loop: el service relee la agenda.
    use_case = ScheduleTaskUseCase(
        repo=repo,
        on_mutation=service.invalidate,
        max_active_tasks=scheduler_cfg.max_tasks_per_agent,
    )
    return SchedulerBundle(
        repo=repo,
        use_case=use_case,
        service=service,
        reconciler=SchedulerReconciler(repo, tz),
    )


def build_scheduler_tool(
    *,
    use_case: ScheduleTaskUseCase,
    runner: IManualTaskRunner,
    agent_id: str,
    user_timezone: str,
    get_channel_context: Callable[[], ChannelContext | None],
) -> SchedulerTool:
    return SchedulerTool(
        schedule_task_uc=use_case,
        manual_runner=runner,
        agent_id=agent_id,
        user_timezone=user_timezone,
        get_channel_context=get_channel_context,
    )


async def reconciliar_builtins(
    bundle: SchedulerBundle,
    *,
    consolidation_schedule: str,
    reconciliaciones: Sequence[tuple[str, str]],
    face_dedup: tuple[str, str] | None,
) -> None:
    """Siembra/actualiza las tareas builtin contra la config actual (al arrancar).

    ``reconciliaciones`` son pares ``(agent_id, schedule)`` de los agentes con
    ``memories.reconciliation.enabled``, en el orden del registry: los IDs de
    tarea se asignan secuencialmente desde ``_RECONCILE_MEMORY_BASE_ID`` y son
    estables porque ese orden es determinista. ``face_dedup`` es
    ``(schedule, agent_id)`` o ``None`` si fotos/dedup no aplica.
    """
    await bundle.reconciler.reconcile_builtin_task(
        build_consolidate_memory_task(consolidation_schedule)
    )
    for idx, (agent_id, schedule) in enumerate(reconciliaciones):
        task_id = _RECONCILE_MEMORY_BASE_ID + idx
        await bundle.reconciler.reconcile_builtin_task(
            build_reconcile_memory_task(schedule=schedule, agent_id=agent_id, task_id=task_id)
        )
        logger.info(
            "reconcile_memory task reconciliada — agente='%s' schedule='%s' task_id=%d",
            agent_id,
            schedule,
            task_id,
        )
    if face_dedup is not None:
        schedule, agent_id = face_dedup
        await bundle.reconciler.reconcile_builtin_task(build_face_dedup_task(schedule, agent_id))
