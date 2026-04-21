"""Integration tests: SQLite migration (created_by column) + count_active_by_agent.

Uses in-memory SQLite (:memory:) for full isolation.
REQs: REQ-ST-12, REQ-ST-7
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

from adapters.outbound.scheduler.sqlite_scheduler_repo import SQLiteSchedulerRepo
from core.domain.entities.task import (
    AgentSendPayload,
    ScheduledTask,
    TaskKind,
    TaskStatus,
    TriggerType,
)


def _make_task(
    name: str = "test",
    created_by: str = "",
    status: TaskStatus = TaskStatus.PENDING,
) -> ScheduledTask:
    return ScheduledTask(
        id=0,
        name=name,
        task_kind=TaskKind.ONESHOT,
        trigger_type=TriggerType.AGENT_SEND,
        trigger_payload=AgentSendPayload(agent_id="agent-x", task="noop"),
        schedule="2026-06-01T10:00:00+00:00",
        created_by=created_by,
        status=status,
    )


@pytest.fixture()
def repo(tmp_path):
    """SQLiteSchedulerRepo backed by in-memory DB via tmp_path."""
    db_path = str(tmp_path / "test_scheduler.db")
    return SQLiteSchedulerRepo(db_path)


# ---------------------------------------------------------------------------
# SC: created_by column added idempotently
# ---------------------------------------------------------------------------


async def test_ensure_schema_idempotent_adds_created_by(repo: SQLiteSchedulerRepo) -> None:
    """Calling ensure_schema twice must not fail and must produce created_by column."""
    await repo.ensure_schema()
    await repo.ensure_schema()  # second call — must be a no-op (idempotent)

    # Verify column exists by inserting a row with created_by
    async with aiosqlite.connect(repo._db_path) as conn:
        conn.row_factory = aiosqlite.Row
        # Insert a minimal row so we can SELECT it back with created_by
        await conn.execute(
            """
            INSERT INTO scheduled_tasks
                (id, name, description, task_kind, trigger_type, trigger_payload,
                 schedule, status, log_enabled, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                200,
                "idempotent-test",
                "",
                "oneshot",
                "agent_send",
                '{"type": "agent_send", "agent_id": "agent-x"}',
                "2026-06-01T10:00:00+00:00",
                "pending",
                1,
                datetime.now(timezone.utc).isoformat(),
                "test-agent",
            ),
        )
        await conn.commit()
        rows = await conn.execute_fetchall("SELECT created_by FROM scheduled_tasks WHERE id = 200")

    assert rows[0]["created_by"] == "test-agent"


# ---------------------------------------------------------------------------
# SC: count_active_by_agent counts only matching agent's non-terminal tasks
# ---------------------------------------------------------------------------


async def test_count_active_by_agent_counts_only_matching_agent(
    repo: SQLiteSchedulerRepo,
) -> None:
    """Rows for agent-a must not be counted when querying for agent-b."""
    await repo.save_task(_make_task("a1", created_by="agent-a"))
    await repo.save_task(_make_task("a2", created_by="agent-a"))
    await repo.save_task(_make_task("b1", created_by="agent-b"))

    count_a = await repo.count_active_by_agent("agent-a")
    count_b = await repo.count_active_by_agent("agent-b")

    assert count_a == 2
    assert count_b == 1


async def test_count_active_by_agent_excludes_terminal_and_disabled(
    repo: SQLiteSchedulerRepo,
) -> None:
    """completed, failed tasks AND tasks with enabled=False must NOT be counted."""
    await repo.save_task(
        _make_task("pending-task", created_by="agent-a", status=TaskStatus.PENDING)
    )
    t_completed = await repo.save_task(_make_task("done", created_by="agent-a"))
    t_failed = await repo.save_task(_make_task("fail", created_by="agent-a"))
    t_disabled = await repo.save_task(_make_task("disabled", created_by="agent-a"))

    # Terminal statuses via update_status; disabled via update_enabled (intent flag)
    await repo.update_status(t_completed.id, TaskStatus.COMPLETED)
    await repo.update_status(t_failed.id, TaskStatus.FAILED)
    await repo.update_enabled(t_disabled.id, False)

    count = await repo.count_active_by_agent("agent-a")

    # Only the pending+enabled task should count
    assert count == 1


async def test_count_active_by_agent_includes_running_and_missed(
    repo: SQLiteSchedulerRepo,
) -> None:
    """running and missed tasks are non-terminal: they MUST be counted."""
    t_running = await repo.save_task(_make_task("running", created_by="agent-a"))
    t_missed = await repo.save_task(_make_task("missed", created_by="agent-a"))
    await repo.update_status(t_running.id, TaskStatus.RUNNING)
    await repo.update_status(t_missed.id, TaskStatus.MISSED)

    count = await repo.count_active_by_agent("agent-a")

    assert count == 2


# ---------------------------------------------------------------------------
# SC: rows with created_by="" don't count against any named agent
# ---------------------------------------------------------------------------


async def test_cli_tasks_do_not_count_against_named_agent(
    repo: SQLiteSchedulerRepo,
) -> None:
    """Tasks with created_by='' (CLI origin) must not pollute any agent's count."""
    # 3 CLI tasks with empty created_by
    await repo.save_task(_make_task("cli-1", created_by=""))
    await repo.save_task(_make_task("cli-2", created_by=""))
    await repo.save_task(_make_task("cli-3", created_by=""))

    # 1 task from a named agent
    await repo.save_task(_make_task("agent-task", created_by="agent-a"))

    count_agent_a = await repo.count_active_by_agent("agent-a")
    count_empty = await repo.count_active_by_agent("")

    # agent-a sees only its own task
    assert count_agent_a == 1
    # empty string resolves to CLI bucket — should NOT be used in guardrail
    # but the count itself is correct: 3 rows with created_by=""
    assert count_empty == 3


async def test_count_active_by_agent_returns_zero_for_unknown_agent(
    repo: SQLiteSchedulerRepo,
) -> None:
    """Querying an agent with no tasks must return 0, not raise."""
    await repo.save_task(_make_task("some-task", created_by="agent-x"))

    count = await repo.count_active_by_agent("agent-unknown")

    assert count == 0
