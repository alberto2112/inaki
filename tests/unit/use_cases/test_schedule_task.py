"""Unit tests for ScheduleTaskUseCase."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.entities.task import (
    ConsolidateMemoryPayload,
    ScheduledTask,
    TaskKind,
    TaskStatus,
    TriggerType,
)
from core.domain.errors import BuiltinTaskProtectedError, TaskNotFoundError
from core.use_cases.schedule_task import ScheduleTaskUseCase


def _make_task(task_id: int = 0) -> ScheduledTask:
    return ScheduledTask(
        id=task_id,
        name="test-task",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule="2025-01-01T03:00:00+00:00",
    )


@pytest.fixture()
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    # Default: la task existe. Los tests que necesitan probar "not found" lo
    # sobreescriben con `mock_repo.get_task.return_value = None`.
    repo.get_task.return_value = _make_task(task_id=150)
    return repo


@pytest.fixture()
def on_mutation() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def uc(mock_repo: AsyncMock, on_mutation: MagicMock) -> ScheduleTaskUseCase:
    return ScheduleTaskUseCase(repo=mock_repo, on_mutation=on_mutation)


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------

async def test_delete_builtin_task_raises(uc: ScheduleTaskUseCase) -> None:
    with pytest.raises(BuiltinTaskProtectedError):
        await uc.delete_task(1)


async def test_delete_builtin_task_id_99_raises(uc: ScheduleTaskUseCase) -> None:
    with pytest.raises(BuiltinTaskProtectedError):
        await uc.delete_task(99)


async def test_delete_user_task_calls_repo(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock, on_mutation: MagicMock
) -> None:
    await uc.delete_task(150)
    mock_repo.delete_task.assert_awaited_once_with(150)
    on_mutation.assert_called_once()


# ---------------------------------------------------------------------------
# on_mutation called after mutations
# ---------------------------------------------------------------------------

async def test_create_task_calls_on_mutation(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock, on_mutation: MagicMock
) -> None:
    task = _make_task()
    mock_repo.save_task.return_value = task
    await uc.create_task(task)
    on_mutation.assert_called_once()


async def test_enable_task_pending_only_flips_flag(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock, on_mutation: MagicMock
) -> None:
    """Una task en PENDING que se habilita solo necesita actualizar el flag."""
    # _make_task por default viene con status=PENDING
    await uc.enable_task(150)
    mock_repo.update_enabled.assert_awaited_once_with(150, True)
    mock_repo.save_task.assert_not_awaited()
    on_mutation.assert_called_once()


async def test_enable_task_from_failed_resets_runtime(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock, on_mutation: MagicMock
) -> None:
    """Una task en FAILED/MISSED que se habilita se resetea a PENDING
    con retry_count=0 y next_run=None para que el scheduler la vuelva a tomar."""
    failed = _make_task(task_id=150).model_copy(update={
        "status": TaskStatus.FAILED,
        "retry_count": 3,
    })
    mock_repo.get_task.return_value = failed
    mock_repo.save_task.side_effect = lambda t: t

    await uc.enable_task(150)

    saved = mock_repo.save_task.await_args[0][0]
    assert saved.enabled is True
    assert saved.status == TaskStatus.PENDING
    assert saved.retry_count == 0
    assert saved.next_run is None
    mock_repo.update_enabled.assert_not_awaited()
    on_mutation.assert_called_once()


async def test_disable_task_flips_enabled_only(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock, on_mutation: MagicMock
) -> None:
    """disable_task NO debe tocar status runtime — solo el flag de intención."""
    await uc.disable_task(150)
    mock_repo.update_enabled.assert_awaited_once_with(150, False)
    mock_repo.update_status.assert_not_awaited()
    on_mutation.assert_called_once()


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------

async def test_get_task_raises_when_not_found(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    mock_repo.get_task.return_value = None
    with pytest.raises(TaskNotFoundError):
        await uc.get_task(999)


async def test_get_task_returns_task(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    task = _make_task(task_id=150)
    mock_repo.get_task.return_value = task
    result = await uc.get_task(150)
    assert result.id == 150


# ---------------------------------------------------------------------------
# update_task — runtime reset on invalidating edits
# ---------------------------------------------------------------------------

async def test_update_task_builtin_raises(uc: ScheduleTaskUseCase) -> None:
    with pytest.raises(BuiltinTaskProtectedError):
        await uc.update_task(1, name="x")


async def test_update_invalidating_field_wakes_failed_task(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    """Editar trigger_payload de una task en 'failed' debe devolverla a 'pending'
    y limpiar retry_count/next_run para que el scheduler la vuelva a tomar."""
    from datetime import datetime, timezone

    existing = _make_task(task_id=150).model_copy(update={
        "status": TaskStatus.FAILED,
        "retry_count": 3,
        "next_run": datetime(2020, 1, 1, tzinfo=timezone.utc),  # stale
    })
    mock_repo.get_task.return_value = existing
    mock_repo.save_task.side_effect = lambda t: t

    await uc.update_task(150, trigger_payload=ConsolidateMemoryPayload())

    saved = mock_repo.save_task.await_args[0][0]
    assert saved.status == TaskStatus.PENDING
    assert saved.retry_count == 0
    assert saved.next_run is None  # repo lo recomputa en save_task


async def test_update_invalidating_field_preserves_disabled_intent(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    """Una task con enabled=False NO debe despertarse (reset a PENDING) solo
    porque se edita el schedule. Respetamos la intención del usuario.
    retry_count/next_run sí se limpian — son runtime stale respecto al schedule nuevo."""
    existing = _make_task(task_id=150).model_copy(update={
        "enabled": False,
        "status": TaskStatus.FAILED,  # estado runtime cualquiera, no importa
        "retry_count": 2,
    })
    mock_repo.get_task.return_value = existing
    mock_repo.save_task.side_effect = lambda t: t

    await uc.update_task(150, schedule="2099-01-01T00:00:00+00:00")

    saved = mock_repo.save_task.await_args[0][0]
    assert saved.enabled is False  # sigue deshabilitada
    assert saved.status == TaskStatus.FAILED  # status runtime NO se resetea
    assert saved.retry_count == 0
    assert saved.next_run is None


async def test_update_non_invalidating_field_no_reset(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    """Cambios cosméticos (name, description) NO tocan el runtime."""
    from datetime import datetime, timezone

    existing_next = datetime(2099, 6, 1, tzinfo=timezone.utc)
    existing = _make_task(task_id=150).model_copy(update={
        "status": TaskStatus.FAILED,
        "retry_count": 3,
        "next_run": existing_next,
    })
    mock_repo.get_task.return_value = existing
    mock_repo.save_task.side_effect = lambda t: t

    await uc.update_task(150, name="renamed")

    saved = mock_repo.save_task.await_args[0][0]
    assert saved.name == "renamed"
    assert saved.status == TaskStatus.FAILED  # no se toca
    assert saved.retry_count == 3
    assert saved.next_run == existing_next


async def test_update_explicit_status_override_respected(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    """Si el caller pasa status explícito junto con un campo invalidante,
    el override gana sobre el reset automático a pending (setdefault)."""
    existing = _make_task(task_id=150).model_copy(update={
        "status": TaskStatus.PENDING,
    })
    mock_repo.get_task.return_value = existing
    mock_repo.save_task.side_effect = lambda t: t

    await uc.update_task(
        150,
        trigger_payload=ConsolidateMemoryPayload(),
        status=TaskStatus.FAILED,  # override explícito
    )

    saved = mock_repo.save_task.await_args[0][0]
    assert saved.status == TaskStatus.FAILED  # no pisado por el reset a PENDING


async def test_update_invalidating_recomputes_for_pending_task(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    """Una task 'pending' con schedule nuevo debe quedar pending pero con
    next_run limpio para que el repo lo recompute desde el cron/ISO nuevo."""
    from datetime import datetime, timezone

    existing = _make_task(task_id=150).model_copy(update={
        "status": TaskStatus.PENDING,
        "next_run": datetime(2020, 1, 1, tzinfo=timezone.utc),  # stale
    })
    mock_repo.get_task.return_value = existing
    mock_repo.save_task.side_effect = lambda t: t

    await uc.update_task(150, schedule="2099-01-01T00:00:00+00:00")

    saved = mock_repo.save_task.await_args[0][0]
    assert saved.status == TaskStatus.PENDING
    assert saved.next_run is None
    assert saved.schedule == "2099-01-01T00:00:00+00:00"
