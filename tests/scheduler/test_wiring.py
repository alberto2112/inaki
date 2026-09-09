"""``inaki.scheduler.wiring``: el módulo se ensambla solo, sin conocer a los agentes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from inaki.config import GlobalConfig
from inaki.scheduler.adapters.builtin_tasks import (
    _RECONCILE_MEMORY_BASE_ID,
    CONSOLIDATE_MEMORY_TASK_ID,
    FACE_DEDUP_TASK_ID,
)
from inaki.scheduler.ports.dispatch import SchedulerDispatchPorts
from inaki.scheduler.wiring import (
    SchedulerBundle,
    build_dispatch_ports,
    build_scheduler,
    reconciliar_builtins,
)


def _global_cfg() -> GlobalConfig:
    return GlobalConfig.model_validate(
        {
            "app": {},
            "llm": {},
            "embedding": {},
            "chat_history": {},
            "memories": {"db_filename": ":memory:"},
            "scheduler": {"db_filename": ":memory:"},
        }
    )


def _dispatch() -> SchedulerDispatchPorts:
    return build_dispatch_ports(
        channel_sender=MagicMock(),
        llm_dispatcher=MagicMock(),
        consolidate_all=MagicMock(),
        reconcilers={},
    )


def test_build_scheduler_devuelve_las_cuatro_piezas_sobre_el_mismo_repo() -> None:
    bundle = build_scheduler(_global_cfg(), dispatch=_dispatch())

    assert isinstance(bundle, SchedulerBundle)
    assert bundle.use_case._repo is bundle.repo
    assert bundle.service._repo is bundle.repo
    assert bundle.reconciler._repo is bundle.repo


def test_una_mutacion_del_crud_invalida_la_vista_del_loop() -> None:
    """El use case no conoce al service: recibe ``on_mutation`` y el wiring lo ata."""
    bundle = build_scheduler(_global_cfg(), dispatch=_dispatch())

    assert bundle.use_case._on_mutation == bundle.service.invalidate


async def test_reconciliar_builtins_asigna_ids_estables_por_orden() -> None:
    """Consolidación, una reconciliación por agente (ids secuenciales) y dedup de caras."""
    reconciler = MagicMock()
    reconciler.reconcile_builtin_task = AsyncMock()
    bundle = SchedulerBundle(
        repo=MagicMock(), use_case=MagicMock(), service=MagicMock(), reconciler=reconciler
    )

    await reconciliar_builtins(
        bundle,
        consolidation_schedule="0 3 * * *",
        reconciliaciones=[("anacleto", "0 4 * * 1"), ("dev", "0 5 * * 1")],
        face_dedup=("0 6 * * *", "anacleto"),
    )

    tareas = [call.args[0] for call in reconciler.reconcile_builtin_task.await_args_list]
    assert [t.id for t in tareas] == [
        CONSOLIDATE_MEMORY_TASK_ID,
        _RECONCILE_MEMORY_BASE_ID,
        _RECONCILE_MEMORY_BASE_ID + 1,
        FACE_DEDUP_TASK_ID,
    ]
    assert tareas[1].trigger_payload.agent_id == "anacleto"
    assert tareas[2].trigger_payload.agent_id == "dev"


async def test_sin_face_dedup_no_se_siembra_esa_tarea() -> None:
    reconciler = MagicMock()
    reconciler.reconcile_builtin_task = AsyncMock()
    bundle = SchedulerBundle(
        repo=MagicMock(), use_case=MagicMock(), service=MagicMock(), reconciler=reconciler
    )

    await reconciliar_builtins(
        bundle, consolidation_schedule="0 3 * * *", reconciliaciones=[], face_dedup=None
    )

    assert reconciler.reconcile_builtin_task.await_count == 1
