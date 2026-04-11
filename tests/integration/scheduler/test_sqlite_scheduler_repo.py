"""Integration tests for SQLiteSchedulerRepo."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from adapters.outbound.scheduler.sqlite_scheduler_repo import SQLiteSchedulerRepo
from core.domain.entities.task import (
    ConsolidateMemoryPayload,
    ScheduledTask,
    TaskKind,
    TaskStatus,
    TriggerType,
)


def _make_task(name: str = "task", next_run: datetime | None = None) -> ScheduledTask:
    return ScheduledTask(
        id=0,
        name=name,
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule="2025-01-01T03:00:00+00:00",
        next_run=next_run,
    )


def _make_builtin() -> ScheduledTask:
    return ScheduledTask(
        id=1,
        name="builtin_task",
        task_kind=TaskKind.RECURRENT,
        trigger_type=TriggerType.CONSOLIDATE_MEMORY,
        trigger_payload=ConsolidateMemoryPayload(),
        schedule="0 3 * * *",
    )


@pytest.fixture()
async def repo(tmp_path: Path) -> SQLiteSchedulerRepo:
    r = SQLiteSchedulerRepo(str(tmp_path / "test.db"))
    await r.ensure_schema()
    return r


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

async def test_ensure_schema_idempotent(tmp_path: Path) -> None:
    r = SQLiteSchedulerRepo(str(tmp_path / "test.db"))
    await r.ensure_schema()
    await r.ensure_schema()  # must not raise


# ---------------------------------------------------------------------------
# User task ID allocation
# ---------------------------------------------------------------------------

async def test_first_user_task_gets_id_100(repo: SQLiteSchedulerRepo) -> None:
    saved = await repo.save_task(_make_task("first"))
    assert saved.id == 100


async def test_second_user_task_gets_id_101(repo: SQLiteSchedulerRepo) -> None:
    first = await repo.save_task(_make_task("first"))
    second = await repo.save_task(_make_task("second"))
    assert first.id == 100
    assert second.id == 101


# ---------------------------------------------------------------------------
# seed_builtin idempotent
# ---------------------------------------------------------------------------

async def test_seed_builtin_idempotent(repo: SQLiteSchedulerRepo) -> None:
    builtin = _make_builtin()
    await repo.seed_builtin(builtin)
    await repo.seed_builtin(builtin)  # must not raise or duplicate
    tasks = await repo.list_tasks()
    builtin_tasks = [t for t in tasks if t.id == 1]
    assert len(builtin_tasks) == 1


async def test_seed_builtin_computes_next_run_for_recurrent(repo: SQLiteSchedulerRepo) -> None:
    """
    Regresión: un builtin RECURRENT sin next_run se sembraba con NULL y
    nunca lo veía list_due_pending. seed_builtin debe calcularlo vía croniter.
    """
    builtin = _make_builtin()
    assert builtin.next_run is None  # precondición del caso de uso

    await repo.seed_builtin(builtin)

    saved = await repo.get_task(1)
    assert saved is not None
    assert saved.next_run is not None
    # Debe ser estrictamente futuro desde "ahora"
    now = datetime.now(timezone.utc)
    assert saved.next_run > now


# ---------------------------------------------------------------------------
# get_next_due
# ---------------------------------------------------------------------------

async def test_get_next_due_returns_soonest(repo: SQLiteSchedulerRepo) -> None:
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    earlier = datetime(2025, 6, 1, 11, 0, 0, tzinfo=timezone.utc)
    later = datetime(2025, 6, 1, 13, 0, 0, tzinfo=timezone.utc)

    await repo.save_task(_make_task("later", next_run=later))
    await repo.save_task(_make_task("earlier", next_run=earlier))

    result = await repo.get_next_due(now)
    # Both are in the future relative to now (both > now), earliest should be returned
    assert result is not None
    assert result.name == "earlier"


# ---------------------------------------------------------------------------
# list_due_pending
# ---------------------------------------------------------------------------

async def test_list_due_pending_returns_only_past_due(repo: SQLiteSchedulerRepo) -> None:
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    past = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    future = datetime(2025, 6, 1, 14, 0, 0, tzinfo=timezone.utc)

    past_task = await repo.save_task(_make_task("past", next_run=past))
    await repo.save_task(_make_task("future", next_run=future))

    due = await repo.list_due_pending(now)
    assert len(due) == 1
    assert due[0].id == past_task.id


async def test_list_due_pending_excludes_disabled(repo: SQLiteSchedulerRepo) -> None:
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    past = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)

    task = await repo.save_task(_make_task("past", next_run=past))
    # Disable it
    await repo.update_status(task.id, TaskStatus.DISABLED)

    due = await repo.list_due_pending(now)
    assert len(due) == 0
