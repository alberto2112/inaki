"""Unit tests for ScheduleTaskUseCase."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.entities.task import (
    CliCommandPayload,
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
        trigger_type=TriggerType.CLI_COMMAND,
        trigger_payload=CliCommandPayload(args=["--hello"]),
        schedule="2025-01-01T03:00:00+00:00",
    )


@pytest.fixture()
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_task.return_value = None
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


async def test_enable_task_calls_on_mutation(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock, on_mutation: MagicMock
) -> None:
    await uc.enable_task(150)
    mock_repo.update_status.assert_awaited_once_with(150, TaskStatus.PENDING)
    on_mutation.assert_called_once()


async def test_disable_task_calls_on_mutation(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock, on_mutation: MagicMock
) -> None:
    await uc.disable_task(150)
    mock_repo.update_status.assert_awaited_once_with(150, TaskStatus.DISABLED)
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
