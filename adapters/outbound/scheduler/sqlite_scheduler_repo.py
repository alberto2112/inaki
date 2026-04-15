"""
SQLiteSchedulerRepo — persistencia de tareas programadas en SQLite.

Sigue exactamente el mismo patrón que sqlite_history_store.py:
  - @asynccontextmanager _conn()
  - ensure_schema() idempotente llamado al inicio de cada método público
  - Datetimes almacenados como ISO strings
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite
from croniter import croniter
from pydantic import TypeAdapter

from core.domain.entities.task import ScheduledTask, TaskKind, TaskStatus, TriggerPayload
from core.domain.entities.task_log import TaskLog

logger = logging.getLogger(__name__)

_CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id                    INTEGER PRIMARY KEY,
    name                  TEXT    NOT NULL,
    description           TEXT    NOT NULL DEFAULT '',
    task_kind             TEXT    NOT NULL,
    trigger_type          TEXT    NOT NULL,
    trigger_payload       TEXT    NOT NULL,
    schedule              TEXT    NOT NULL,
    next_run              REAL,
    status                TEXT    NOT NULL DEFAULT 'pending',
    enabled               INTEGER NOT NULL DEFAULT 1,
    executions_remaining  INTEGER,
    retry_count           INTEGER NOT NULL DEFAULT 0,
    log_enabled           INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT    NOT NULL,
    last_run              TEXT,
    created_by            TEXT    DEFAULT ''
);
"""

_CREATE_TASKS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_tasks_due ON scheduled_tasks(enabled, status, next_run);
"""

_CREATE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS task_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES scheduled_tasks(id),
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    status      TEXT    NOT NULL,
    output      TEXT,
    error       TEXT,
    metadata    TEXT
);
"""

_PAYLOAD_ADAPTER: TypeAdapter[TriggerPayload] = TypeAdapter(TriggerPayload)  # type: ignore[type-arg]


def _serialize_payload(payload: TriggerPayload) -> str:  # type: ignore[type-arg]
    return _PAYLOAD_ADAPTER.dump_json(payload).decode()


def _deserialize_payload(raw: str) -> TriggerPayload:  # type: ignore[type-arg]
    return _PAYLOAD_ADAPTER.validate_json(raw)


class SQLiteSchedulerRepo:

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def ensure_schema(self) -> None:
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)

    async def save_task(self, task: ScheduledTask) -> ScheduledTask:
        payload_json = _serialize_payload(task.trigger_payload)
        next_run_ts: float | None = task.next_run.timestamp() if task.next_run else None

        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            if task.id == 0:
                # Allocate user task id: COALESCE(MAX(id), 99) + 1 WHERE id >= 100
                row = await conn.execute_fetchall(
                    "SELECT COALESCE(MAX(id), 99) + 1 AS next_id FROM scheduled_tasks WHERE id >= 100"
                )
                new_id = row[0]["next_id"] if row else 100
                await conn.execute(
                    """
                    INSERT INTO scheduled_tasks
                        (id, name, description, task_kind, trigger_type, trigger_payload,
                         schedule, next_run, status, enabled, executions_remaining,
                         retry_count, log_enabled, created_at, last_run, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id,
                        task.name,
                        task.description,
                        task.task_kind.value,
                        task.trigger_type.value,
                        payload_json,
                        task.schedule,
                        next_run_ts,
                        task.status.value,
                        int(task.enabled),
                        task.executions_remaining,
                        task.retry_count,
                        int(task.log_enabled),
                        task.created_at.isoformat(),
                        task.last_run.isoformat() if task.last_run else None,
                        task.created_by,
                    ),
                )
                await conn.commit()
                return task.model_copy(update={"id": new_id})
            else:
                # Upsert by explicit id
                await conn.execute(
                    """
                    INSERT INTO scheduled_tasks
                        (id, name, description, task_kind, trigger_type, trigger_payload,
                         schedule, next_run, status, enabled, executions_remaining,
                         retry_count, log_enabled, created_at, last_run, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        description=excluded.description,
                        task_kind=excluded.task_kind,
                        trigger_type=excluded.trigger_type,
                        trigger_payload=excluded.trigger_payload,
                        schedule=excluded.schedule,
                        next_run=excluded.next_run,
                        status=excluded.status,
                        enabled=excluded.enabled,
                        executions_remaining=excluded.executions_remaining,
                        retry_count=excluded.retry_count,
                        log_enabled=excluded.log_enabled,
                        created_at=excluded.created_at,
                        last_run=excluded.last_run,
                        created_by=excluded.created_by
                    """,
                    (
                        task.id,
                        task.name,
                        task.description,
                        task.task_kind.value,
                        task.trigger_type.value,
                        payload_json,
                        task.schedule,
                        next_run_ts,
                        task.status.value,
                        int(task.enabled),
                        task.executions_remaining,
                        task.retry_count,
                        int(task.log_enabled),
                        task.created_at.isoformat(),
                        task.last_run.isoformat() if task.last_run else None,
                        task.created_by,
                    ),
                )
                await conn.commit()
                return task

    async def get_task(self, task_id: int) -> ScheduledTask | None:
        """Devuelve la tarea o None si no existe."""
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = await conn.execute_fetchall(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
            )
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def list_tasks(self) -> list[ScheduledTask]:
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = await conn.execute_fetchall(
                "SELECT * FROM scheduled_tasks ORDER BY id ASC"
            )
        return [self._row_to_task(row) for row in rows]

    async def delete_task(self, task_id: int) -> None:
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            await conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            await conn.commit()

    async def count_active_by_agent(self, agent_id: str) -> int:
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = await conn.execute_fetchall(
                """
                SELECT COUNT(*) AS cnt FROM scheduled_tasks
                WHERE created_by = ? AND status NOT IN ('completed', 'failed', 'disabled')
                """,
                (agent_id,),
            )
        return rows[0]["cnt"] if rows else 0

    async def get_next_due(self, as_of: datetime) -> ScheduledTask | None:
        """Returns the enabled pending task with the earliest next_run."""
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = await conn.execute_fetchall(
                """
                SELECT * FROM scheduled_tasks
                WHERE enabled = 1 AND status = 'pending'
                ORDER BY next_run ASC NULLS LAST
                LIMIT 1
                """,
            )
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def list_due_pending(self, as_of: datetime) -> list[ScheduledTask]:
        """Returns all enabled pending tasks whose next_run <= as_of."""
        ts = as_of.timestamp()
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = await conn.execute_fetchall(
                """
                SELECT * FROM scheduled_tasks
                WHERE enabled = 1 AND status = 'pending' AND next_run <= ?
                ORDER BY next_run ASC
                """,
                (ts,),
            )
        return [self._row_to_task(row) for row in rows]

    async def update_status(self, task_id: int, status: TaskStatus, *, retry_count: int | None = None) -> None:
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            if retry_count is not None:
                await conn.execute(
                    "UPDATE scheduled_tasks SET status = ?, retry_count = ? WHERE id = ?",
                    (status.value, retry_count, task_id),
                )
            else:
                await conn.execute(
                    "UPDATE scheduled_tasks SET status = ? WHERE id = ?",
                    (status.value, task_id),
                )
            await conn.commit()

    async def update_after_execution(
        self,
        task_id: int,
        *,
        success: bool,
        output: str | None,
        next_run: datetime | None,
        executions_remaining: int | None,
        retry_count: int = 0,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        next_run_ts = next_run.timestamp() if next_run else None
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            await conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'pending',
                    last_run = ?,
                    next_run = ?,
                    executions_remaining = ?,
                    retry_count = ?
                WHERE id = ?
                """,
                (now_iso, next_run_ts, executions_remaining, retry_count, task_id),
            )
            await conn.commit()

    async def save_log(self, log: TaskLog) -> TaskLog:
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            metadata_json = json.dumps(log.metadata) if log.metadata else None
            cursor = await conn.execute(
                """
                INSERT INTO task_logs
                    (task_id, started_at, finished_at, status, output, error, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log.task_id,
                    log.started_at.isoformat(),
                    log.finished_at.isoformat() if log.finished_at else None,
                    log.status,
                    log.output,
                    log.error,
                    metadata_json,
                ),
            )
            await conn.commit()
            return log.model_copy(update={"id": cursor.lastrowid})

    async def seed_builtin(self, task: ScheduledTask) -> None:
        """
        Insert builtin task only if it doesn't already exist (INSERT OR IGNORE).

        If the task is RECURRENT and `next_run` is None, compute it from the
        cron schedule so the task is actually due at some point. Otherwise it
        would sit in the DB with `next_run = NULL` and never be picked up by
        `list_due_pending` (NULL fails the `next_run <= ?` predicate).
        """
        payload_json = _serialize_payload(task.trigger_payload)
        next_run = task.next_run
        if next_run is None and task.task_kind == TaskKind.RECURRENT:
            now = datetime.now(timezone.utc)
            next_run = datetime.fromtimestamp(
                croniter(task.schedule, now).get_next(), tz=timezone.utc
            )
        next_run_ts: float | None = next_run.timestamp() if next_run else None
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            await conn.execute(
                """
                INSERT OR IGNORE INTO scheduled_tasks
                    (id, name, description, task_kind, trigger_type, trigger_payload,
                     schedule, next_run, status, enabled, executions_remaining,
                     retry_count, log_enabled, created_at, last_run, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.name,
                    task.description,
                    task.task_kind.value,
                    task.trigger_type.value,
                    payload_json,
                    task.schedule,
                    next_run_ts,
                    task.status.value,
                    int(task.enabled),
                    task.executions_remaining,
                    task.retry_count,
                    int(task.log_enabled),
                    task.created_at.isoformat(),
                    task.last_run.isoformat() if task.last_run else None,
                    task.created_by,
                ),
            )
            await conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_schema_conn(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(_CREATE_TASKS_TABLE)
        await conn.execute(_CREATE_TASKS_INDEX)
        await conn.execute(_CREATE_LOGS_TABLE)
        await conn.commit()

    def _row_to_task(self, row: aiosqlite.Row) -> ScheduledTask:
        next_run: datetime | None = None
        if row["next_run"] is not None:
            next_run = datetime.fromtimestamp(row["next_run"], tz=timezone.utc)

        last_run: datetime | None = None
        if row["last_run"]:
            last_run = datetime.fromisoformat(row["last_run"])

        return ScheduledTask(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            task_kind=row["task_kind"],
            trigger_type=row["trigger_type"],
            trigger_payload=_deserialize_payload(row["trigger_payload"]),
            schedule=row["schedule"],
            next_run=next_run,
            status=row["status"],
            enabled=bool(row["enabled"]),
            executions_remaining=row["executions_remaining"],
            retry_count=row["retry_count"],
            log_enabled=bool(row["log_enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_run=last_run,
            created_by=row["created_by"],
        )
