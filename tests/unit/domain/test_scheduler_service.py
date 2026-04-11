"""Unit tests for SchedulerService."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time

from core.domain.entities.task import (
    ConsolidateMemoryPayload,
    ScheduledTask,
    TaskKind,
    TaskStatus,
    TriggerType,
)
from core.domain.entities.task_log import TaskLog
from core.domain.services.scheduler_service import SchedulerService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.max_retries = 1
    cfg.output_truncation_size = 65536
    return cfg


def _make_dispatch() -> MagicMock:
    dispatch = MagicMock()
    dispatch.channel_sender = AsyncMock()
    dispatch.llm_dispatcher = AsyncMock()
    dispatch.consolidator = AsyncMock()
    dispatch.consolidator.consolidate_all = AsyncMock(return_value="ok")
    return dispatch


def _make_task(
    task_id: int = 100,
    task_kind: TaskKind = TaskKind.ONESHOT,
    next_run: datetime | None = None,
    executions_remaining: int | None = None,
) -> ScheduledTask:
    return ScheduledTask(
        id=task_id,
        name="test",
        task_kind=task_kind,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule="0 3 * * *",
        next_run=next_run,
        executions_remaining=executions_remaining,
        status=TaskStatus.PENDING,
    )


@pytest.fixture()
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.list_due_pending.return_value = []
    repo.get_next_due.return_value = None
    return repo


@pytest.fixture()
def service(mock_repo: AsyncMock) -> SchedulerService:
    return SchedulerService(
        repo=mock_repo,
        dispatch=_make_dispatch(),
        config=_make_config(),
    )


# ---------------------------------------------------------------------------
# _handle_missed_on_startup
# ---------------------------------------------------------------------------

@freeze_time("2025-06-01 12:00:00")
async def test_handle_missed_marks_oneshot_as_missed(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    task = _make_task(
        task_id=100,
        task_kind=TaskKind.ONESHOT,
        next_run=datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    mock_repo.list_due_pending.return_value = [task]

    await service._handle_missed_on_startup()

    mock_repo.update_status.assert_awaited_once_with(100, TaskStatus.MISSED)
    mock_repo.save_log.assert_awaited_once()
    log_arg: TaskLog = mock_repo.save_log.call_args[0][0]
    assert log_arg.status == "missed"
    assert log_arg.task_id == 100


@freeze_time("2025-06-01 12:00:00")
async def test_handle_missed_recomputes_recurrent_next_run(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task(
        task_id=101,
        task_kind=TaskKind.RECURRENT,
        next_run=datetime(2025, 6, 1, 3, 0, 0, tzinfo=timezone.utc),
    )
    task = task.model_copy(update={"schedule": "0 3 * * *"})
    mock_repo.list_due_pending.return_value = [task]

    await service._handle_missed_on_startup()

    mock_repo.update_after_execution.assert_awaited_once()
    call_kwargs = mock_repo.update_after_execution.call_args.kwargs
    # next_run should be future (tomorrow 03:00 UTC)
    assert call_kwargs["next_run"] > datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _execute_task retries
# ---------------------------------------------------------------------------

async def test_execute_task_retries_max_retries_then_failed(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task()
    # Make _dispatch_trigger raise every time
    service._dispatch_trigger = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    await service._execute_task(task)

    # max_retries=1 means 2 attempts total (attempt 0 + attempt 1)
    assert service._dispatch_trigger.call_count == 2  # type: ignore[attr-defined]
    # retry_count is persisted after each failure and on final FAILED status
    calls = mock_repo.update_status.call_args_list
    statuses = [(c.args[1], c.kwargs.get("retry_count")) for c in calls]
    assert (TaskStatus.RUNNING, None) in statuses
    assert (TaskStatus.FAILED, 2) in statuses


async def test_execute_task_on_success_calls_finalize(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task()
    service._dispatch_trigger = AsyncMock(return_value="output")  # type: ignore[method-assign]
    service._finalize_task = AsyncMock()  # type: ignore[method-assign]

    await service._execute_task(task)

    service._finalize_task.assert_awaited_once_with(task, "output")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _finalize_task oneshot → COMPLETED
# ---------------------------------------------------------------------------

async def test_finalize_oneshot_sets_completed(
    service: SchedulerService, mock_repo: AsyncMock
) -> None:
    task = _make_task(task_kind=TaskKind.ONESHOT)
    await service._finalize_task(task, None)
    mock_repo.update_status.assert_awaited_once_with(task.id, TaskStatus.COMPLETED)


# ---------------------------------------------------------------------------
# invalidate sets wake event
# ---------------------------------------------------------------------------

def test_invalidate_sets_wake_event(service: SchedulerService) -> None:
    assert not service._wake.is_set()
    service.invalidate()
    assert service._wake.is_set()
