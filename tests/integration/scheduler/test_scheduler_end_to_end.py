"""End-to-end integration tests for SchedulerService + SQLiteSchedulerRepo."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from adapters.outbound.scheduler.sqlite_scheduler_repo import SQLiteSchedulerRepo
from core.domain.entities.task import (
    AgentSendPayload,
    ConsolidateMemoryPayload,
    ScheduledTask,
    TaskKind,
    TaskStatus,
    TriggerType,
)
from core.domain.services.scheduler_service import SchedulerService


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.max_retries = 0  # no retries for speed in tests
    cfg.output_truncation_size = 65536
    return cfg


def _make_dispatch(llm_output: str = "agent-result") -> MagicMock:
    dispatch = MagicMock()
    dispatch.channel_sender = AsyncMock()
    dispatch.llm_dispatcher = AsyncMock(return_value=llm_output)
    dispatch.consolidator = AsyncMock()
    dispatch.consolidator.consolidate_all = AsyncMock(return_value="ok")
    return dispatch


@pytest.fixture()
async def repo(tmp_path: Path) -> SQLiteSchedulerRepo:
    r = SQLiteSchedulerRepo(str(tmp_path / "sched.db"))
    await r.ensure_schema()
    return r


@pytest.fixture()
def dispatch() -> MagicMock:
    return _make_dispatch()


@pytest.fixture()
def service(repo: SQLiteSchedulerRepo, dispatch: MagicMock) -> SchedulerService:
    return SchedulerService(repo=repo, dispatch=dispatch, config=_make_config())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _oneshot_past() -> ScheduledTask:
    return ScheduledTask(
        id=0,
        name="oneshot",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule="2025-06-01T10:00:00+00:00",
        next_run=datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
    )


def _recurrent_past(executions_remaining: int | None = None) -> ScheduledTask:
    return ScheduledTask(
        id=0,
        name="recurrent",
        task_kind=TaskKind.RECURRENT,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule="0 * * * *",  # every hour
        next_run=datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
        executions_remaining=executions_remaining,
    )


# ---------------------------------------------------------------------------
# Oneshot completes after one execution
# ---------------------------------------------------------------------------

@freeze_time("2025-06-01 12:00:00")
async def test_oneshot_completes_after_execution(
    service: SchedulerService,
    repo: SQLiteSchedulerRepo,
    dispatch: MagicMock,
) -> None:
    task = await repo.save_task(_oneshot_past())
    # Patch dispatch trigger to succeed immediately
    service._dispatch_trigger = AsyncMock(return_value=(None, None))  # type: ignore[method-assign]

    await service._run_once()

    saved = await repo.get_task(task.id)
    assert saved is not None
    assert saved.status == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# Recurrent recomputes next_run after execution
# ---------------------------------------------------------------------------

@freeze_time("2025-06-01 12:00:00")
async def test_recurrent_recomputes_next_run(
    service: SchedulerService,
    repo: SQLiteSchedulerRepo,
) -> None:
    task = await repo.save_task(_recurrent_past())
    service._dispatch_trigger = AsyncMock(return_value=("output", None))  # type: ignore[method-assign]

    await service._run_once()

    saved = await repo.get_task(task.id)
    assert saved is not None
    # next_run should now be in the future (13:00 UTC, next hour)
    assert saved.next_run is not None
    assert saved.next_run > datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Countdown hits 0 → COMPLETED
# ---------------------------------------------------------------------------

@freeze_time("2025-06-01 12:00:00")
async def test_recurrent_countdown_hits_zero_then_completed(
    service: SchedulerService,
    repo: SQLiteSchedulerRepo,
) -> None:
    task = await repo.save_task(_recurrent_past(executions_remaining=1))
    service._dispatch_trigger = AsyncMock(return_value=(None, None))  # type: ignore[method-assign]

    await service._run_once()

    saved = await repo.get_task(task.id)
    assert saved is not None
    assert saved.status == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# Missed oneshot on restart → MISSED
# ---------------------------------------------------------------------------

@freeze_time("2025-06-01 12:00:00")
async def test_missed_oneshot_on_restart_marked_missed(
    service: SchedulerService,
    repo: SQLiteSchedulerRepo,
) -> None:
    task = await repo.save_task(_oneshot_past())

    await service._handle_missed_on_startup()

    saved = await repo.get_task(task.id)
    assert saved is not None
    assert saved.status == TaskStatus.MISSED


# ---------------------------------------------------------------------------
# AgentSend with no output_channel → output stored in task_logs
# ---------------------------------------------------------------------------

@freeze_time("2025-06-01 12:00:00")
async def test_agent_send_no_output_channel_stores_output(
    service: SchedulerService,
    repo: SQLiteSchedulerRepo,
    dispatch: MagicMock,
) -> None:
    dispatch.llm_dispatcher.dispatch = AsyncMock(return_value="agent output")
    task = ScheduledTask(
        id=0,
        name="agent-task",
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.AGENT_SEND,
        trigger_payload=AgentSendPayload(
            agent_id="general",
            task="do something",
            output_channel=None,
        ),
        schedule="2025-06-01T10:00:00+00:00",
        next_run=datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    saved_task = await repo.save_task(task)

    await service._run_once()

    # Check that a task_log was written with the output
    import aiosqlite
    async with aiosqlite.connect(repo._db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(
            "SELECT * FROM task_logs WHERE task_id = ?", (saved_task.id,)
        )
    assert len(rows) >= 1
    success_logs = [r for r in rows if r["status"] == "success"]
    assert len(success_logs) == 1
    assert success_logs[0]["output"] == "agent output"
