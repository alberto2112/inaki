"""Unit tests for guardrail in ScheduleTaskUseCase.create_task (REQ-ST-7 / SC-ST-4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.entities.task import (
    ConsolidateMemoryPayload,
    ScheduledTask,
    TaskKind,
    TriggerType,
)
from core.domain.errors import TooManyActiveTasksError
from core.use_cases.schedule_task import ScheduleTaskUseCase


def _make_agent_task(agent_id: str = "agent-main") -> ScheduledTask:
    return ScheduledTask(
        id=0,
        name="test-task",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule="2026-06-01T10:00:00+00:00",
        created_by=agent_id,
    )


def _make_cli_task() -> ScheduledTask:
    """Task with empty created_by — simulates CLI origin."""
    return ScheduledTask(
        id=0,
        name="cli-task",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule="2026-06-01T10:00:00+00:00",
        created_by="",
    )


@pytest.fixture()
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.save_task.side_effect = lambda task: task
    return repo


@pytest.fixture()
def on_mutation() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def uc(mock_repo: AsyncMock, on_mutation: MagicMock) -> ScheduleTaskUseCase:
    return ScheduleTaskUseCase(repo=mock_repo, on_mutation=on_mutation)


# ---------------------------------------------------------------------------
# Guardrail: count >= 21 raises TooManyActiveTasksError
# ---------------------------------------------------------------------------

async def test_guardrail_raises_when_count_at_limit(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    """count_active_by_agent returns 21 → TooManyActiveTasksError raised."""
    mock_repo.count_active_by_agent.return_value = 21
    task = _make_agent_task("agent-main")

    with pytest.raises(TooManyActiveTasksError) as exc_info:
        await uc.create_task(task)

    assert "agent-main" in str(exc_info.value)
    mock_repo.save_task.assert_not_awaited()


async def test_guardrail_raises_when_count_above_limit(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    """count_active_by_agent returns 22 → TooManyActiveTasksError raised."""
    mock_repo.count_active_by_agent.return_value = 22
    task = _make_agent_task("agent-x")

    with pytest.raises(TooManyActiveTasksError):
        await uc.create_task(task)

    mock_repo.save_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# Guardrail: count < 21 allows task creation
# ---------------------------------------------------------------------------

async def test_guardrail_allows_when_count_below_limit(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock, on_mutation: MagicMock
) -> None:
    """count_active_by_agent returns 20 → task saved successfully."""
    mock_repo.count_active_by_agent.return_value = 20
    task = _make_agent_task("agent-main")
    mock_repo.save_task.return_value = task

    result = await uc.create_task(task)

    mock_repo.save_task.assert_awaited_once_with(task)
    on_mutation.assert_called_once()
    assert result is task


async def test_guardrail_allows_when_count_zero(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    """count_active_by_agent returns 0 → task saved successfully."""
    mock_repo.count_active_by_agent.return_value = 0
    task = _make_agent_task("agent-fresh")
    mock_repo.save_task.return_value = task

    await uc.create_task(task)

    mock_repo.save_task.assert_awaited_once()


# ---------------------------------------------------------------------------
# Guardrail: CLI tasks (created_by="") skip the guard entirely
# ---------------------------------------------------------------------------

async def test_cli_task_skips_guardrail(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock, on_mutation: MagicMock
) -> None:
    """CLI tasks (created_by='') must NOT call count_active_by_agent."""
    task = _make_cli_task()
    mock_repo.save_task.return_value = task

    await uc.create_task(task)

    mock_repo.count_active_by_agent.assert_not_awaited()
    mock_repo.save_task.assert_awaited_once_with(task)
    on_mutation.assert_called_once()


async def test_cli_task_skips_guardrail_even_when_count_would_exceed(
    uc: ScheduleTaskUseCase, mock_repo: AsyncMock
) -> None:
    """Even if count would be >= 21, CLI tasks bypass the guard completely."""
    mock_repo.count_active_by_agent.return_value = 999  # would trigger if called
    task = _make_cli_task()
    mock_repo.save_task.return_value = task

    # Must not raise
    await uc.create_task(task)

    mock_repo.count_active_by_agent.assert_not_awaited()
    mock_repo.save_task.assert_awaited_once()
