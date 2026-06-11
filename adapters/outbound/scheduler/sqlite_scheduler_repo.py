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
from typing import AsyncIterator

import aiosqlite
from pydantic import TypeAdapter

from core.domain.entities.task import (
    USER_TASK_ID_START,
    ScheduledTask,
    TaskKind,
    TaskStatus,
    TriggerPayload,
)
from core.domain.entities.task_log import TaskLog
from core.domain.errors import InvalidScheduleError
from core.domain.utils.cron import next_cron_occurrence, resolve_timezone

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
    def __init__(self, db_path: str, user_timezone: str = "UTC") -> None:
        """
        Args:
            db_path: Ruta al archivo SQLite.
            user_timezone: IANA timezone (ej. "Europe/Madrid"). Las expresiones
                cron de tareas RECURRENT se evalúan en esta zona horaria, no en
                UTC. Esto significa que `0 6 * * *` corre a las 6:00 hora del
                usuario, respetando DST. Para ONESHOT no aplica (el schedule
                ya viene como ISO 8601 con offset explícito).
        """
        self._db_path = db_path
        self._cron_tz = resolve_timezone(user_timezone)
        # Las DDL + migración legacy corren UNA vez por instancia, no en cada query.
        self._schema_ready = False
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
        resolved_next_run = self._resolve_next_run(task)
        next_run_ts: float | None = resolved_next_run.timestamp() if resolved_next_run else None

        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            if task.id == 0:
                # Asignación atómica del id de usuario en un solo INSERT...SELECT:
                #   - sin ventana SELECT-then-INSERT (dos creates concurrentes ya
                #     no pueden chocar contra UNIQUE);
                #   - el máximo considera TAMBIÉN task_logs.task_id, así un id
                #     nunca se reusa tras borrar la task más alta (los logs
                #     históricos de la task borrada no se "heredan").
                cursor = await conn.execute(
                    f"""
                    INSERT INTO scheduled_tasks
                        (id, name, description, task_kind, trigger_type, trigger_payload,
                         schedule, next_run, status, enabled, executions_remaining,
                         retry_count, log_enabled, created_at, last_run, created_by)
                    SELECT MAX(
                            {USER_TASK_ID_START - 1},
                            (SELECT COALESCE(MAX(id), 0) FROM scheduled_tasks
                             WHERE id >= {USER_TASK_ID_START}),
                            (SELECT COALESCE(MAX(task_id), 0) FROM task_logs
                             WHERE task_id >= {USER_TASK_ID_START})
                        ) + 1,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    """,
                    (
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
                new_id = cursor.lastrowid
                await conn.commit()
                return task.model_copy(update={"id": new_id, "next_run": resolved_next_run})
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
                return task.model_copy(update={"next_run": resolved_next_run})

    async def get_task(self, task_id: int) -> ScheduledTask | None:
        """Devuelve la tarea o None si no existe."""
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = list(
                await conn.execute_fetchall(
                    "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
                )
            )
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def list_tasks(self) -> list[ScheduledTask]:
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = await conn.execute_fetchall("SELECT * FROM scheduled_tasks ORDER BY id ASC")
        return [self._row_to_task(row) for row in rows]

    async def delete_task(self, task_id: int) -> None:
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            await conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            await conn.commit()

    async def count_active_by_agent(self, agent_id: str) -> int:
        """Cuenta las tareas que ocupan cuota: pending o running.

        Las terminales (completed/failed/missed) no cuentan — un oneshot
        perdido no debe comerse el límite del agente para siempre.
        """
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = list(
                await conn.execute_fetchall(
                    """
                SELECT COUNT(*) AS cnt FROM scheduled_tasks
                WHERE created_by = ? AND enabled = 1 AND status IN ('pending', 'running')
                """,
                    (agent_id,),
                )
            )
        return rows[0]["cnt"] if rows else 0

    async def get_next_due(self) -> ScheduledTask | None:
        """Returns the enabled pending task with the earliest next_run.

        Excluye next_run NULL — coherente con list_due_pending: una fila sin
        next_run es invisible para el loop (no "due ya mismo").
        """
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = list(
                await conn.execute_fetchall(
                    """
                SELECT * FROM scheduled_tasks
                WHERE enabled = 1 AND status = 'pending' AND next_run IS NOT NULL
                ORDER BY next_run ASC
                LIMIT 1
                """,
                )
            )
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def list_running(self) -> list[ScheduledTask]:
        """Tareas atrapadas en RUNNING (el daemon murió a mitad de ejecución)."""
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = await conn.execute_fetchall(
                "SELECT * FROM scheduled_tasks WHERE status = 'running'"
            )
        return [self._row_to_task(row) for row in rows]

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

    async def update_status(
        self, task_id: int, status: TaskStatus, *, retry_count: int | None = None
    ) -> None:
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

    async def update_enabled(self, task_id: int, enabled: bool) -> None:
        """Actualiza SOLO el flag `enabled`. No toca `status` ni runtime state.

        Es la intención declarada del usuario ("quiero/no quiero que corra"),
        ortogonal al estado runtime que maneja el scheduler.
        """
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            await conn.execute(
                "UPDATE scheduled_tasks SET enabled = ? WHERE id = ?",
                (int(enabled), task_id),
            )
            await conn.commit()

    async def update_after_execution(
        self,
        task_id: int,
        *,
        next_run: datetime | None,
        executions_remaining: int | None,
        retry_count: int = 0,
        last_run: datetime | None = None,
    ) -> None:
        """Re-arma una recurrente: status vuelve a 'pending' con el próximo slot.

        ``last_run`` solo se escribe si viene informado — los paths que avanzan
        el cron SIN ejecutar (missed/recovery) pasan None y no mienten last_run.
        """
        next_run_ts = next_run.timestamp() if next_run else None
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            if last_run is not None:
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
                    (last_run.isoformat(), next_run_ts, executions_remaining, retry_count, task_id),
                )
            else:
                await conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET status = 'pending',
                        next_run = ?,
                        executions_remaining = ?,
                        retry_count = ?
                    WHERE id = ?
                    """,
                    (next_run_ts, executions_remaining, retry_count, task_id),
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

    async def list_logs(
        self,
        task_id: int | None,
        limit: int = 10,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> list[TaskLog]:
        """
        Lista logs más-recientes-primero, con paginación.

        Si ``task_id`` es ``None``, lista todos los logs de la tabla (global).
        Si es un entero, filtra por esa tarea.

        Orden estable: `started_at DESC, id DESC` — el tiebreaker por id garantiza
        que `offset` produce páginas reproducibles aun cuando dos logs caen en el
        mismo timestamp. Devuelve TaskLog completo (sin truncación) — el tool es
        el único responsable de decidir qué ve el LLM.
        """
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            if task_id is not None:
                if status_filter is not None:
                    rows = await conn.execute_fetchall(
                        """
                        SELECT * FROM task_logs
                        WHERE task_id = ? AND status = ?
                        ORDER BY started_at DESC, id DESC
                        LIMIT ? OFFSET ?
                        """,
                        (task_id, status_filter, limit, offset),
                    )
                else:
                    rows = await conn.execute_fetchall(
                        """
                        SELECT * FROM task_logs
                        WHERE task_id = ?
                        ORDER BY started_at DESC, id DESC
                        LIMIT ? OFFSET ?
                        """,
                        (task_id, limit, offset),
                    )
            elif status_filter is not None:
                rows = await conn.execute_fetchall(
                    """
                    SELECT * FROM task_logs
                    WHERE status = ?
                    ORDER BY started_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (status_filter, limit, offset),
                )
            else:
                rows = await conn.execute_fetchall(
                    """
                    SELECT * FROM task_logs
                    ORDER BY started_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
        return [self._row_to_tasklog(row) for row in rows]

    async def get_log(self, log_id: int) -> TaskLog | None:
        """Devuelve el TaskLog por id, o None si no existe (sin excepción)."""
        async with self._conn() as conn:
            await self._ensure_schema_conn(conn)
            rows = list(
                await conn.execute_fetchall("SELECT * FROM task_logs WHERE id = ?", (log_id,))
            )
        if not rows:
            return None
        return self._row_to_tasklog(rows[0])

    async def seed_builtin(self, task: ScheduledTask) -> None:
        """
        Insert builtin task only if it doesn't already exist (INSERT OR IGNORE).

        La invariante "RECURRENT/ONESHOT enabled+pending nunca se persiste con
        next_run=NULL" se centraliza en `_resolve_next_run` y se aplica también
        en `save_task`.
        """
        payload_json = _serialize_payload(task.trigger_payload)
        resolved_next_run = self._resolve_next_run(task)
        next_run_ts: float | None = resolved_next_run.timestamp() if resolved_next_run else None
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

    def _resolve_next_run(self, task: ScheduledTask) -> datetime | None:
        """
        Resuelve `next_run` para una tarea que puede venir con None.

        - Si `task.next_run` ya está seteado, lo devuelve sin tocar.
        - RECURRENT sin next_run → se computa la próxima ocurrencia con croniter
          desde `task.schedule`.
        - ONESHOT sin next_run → se parsea `task.schedule` como ISO 8601
          (el contrato upstream garantiza que ya vino validado).

        Invariante que sostiene esto: una tarea enabled+pending NUNCA debe
        persistirse con next_run=NULL. El loop del scheduler filtra por
        `WHERE next_run <= ?` y en SQLite `NULL <= x` evalúa a NULL (falso),
        así que una fila con NULL queda invisible para siempre.
        """
        if task.next_run is not None:
            return task.next_run
        if task.task_kind == TaskKind.RECURRENT:
            # Cron se evalúa en la timezone del usuario via el helper central —
            # `0 6 * * *` significa "6:00 hora local" siempre, con DST. El
            # resultado vuelve en UTC (el loop del scheduler compara en UTC).
            return next_cron_occurrence(task.schedule, self._cron_tz)
        if task.task_kind == TaskKind.ONESHOT:
            try:
                dt = datetime.fromisoformat(task.schedule)
            except ValueError:
                # Antes devolvía None y la fila se persistía con next_run NULL
                # en silencio (invisible para el loop). Fallar acá es honesto.
                raise InvalidScheduleError(
                    f"One-shot schedule '{task.schedule}' is not a valid ISO 8601 datetime."
                ) from None
            # Safety net: si llega un datetime naive, tratarlo como UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return None

    async def _ensure_schema_conn(self, conn: aiosqlite.Connection) -> None:
        if self._schema_ready:
            return
        await conn.execute(_CREATE_TASKS_TABLE)
        await conn.execute(_CREATE_TASKS_INDEX)
        await conn.execute(_CREATE_LOGS_TABLE)
        # Migración idempotente: el valor 'disabled' del enum TaskStatus fue
        # eliminado. Las filas viejas con ese status se mapean a
        # enabled=0, status='pending' (respeta la intención del usuario y
        # deja el runtime en un estado válido que el loop no va a levantar
        # porque enabled=0 lo excluye).
        await conn.execute(
            "UPDATE scheduled_tasks SET enabled = 0, status = 'pending' WHERE status = 'disabled'"
        )
        await conn.commit()
        self._schema_ready = True

    def _row_to_tasklog(self, row: aiosqlite.Row) -> TaskLog:
        started_at = datetime.fromisoformat(row["started_at"])
        finished_at: datetime | None = (
            datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
        )
        metadata = json.loads(row["metadata"]) if row["metadata"] else None
        return TaskLog(
            id=row["id"],
            task_id=row["task_id"],
            started_at=started_at,
            finished_at=finished_at,
            status=row["status"],
            output=row["output"],
            error=row["error"],
            metadata=metadata,
        )

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
