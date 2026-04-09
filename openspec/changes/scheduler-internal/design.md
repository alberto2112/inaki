# Design: scheduler-internal

## 1. Architecture Overview

The scheduler is a hexagonal slice that lives alongside the existing history/memory/skills slices. It follows the same layered structure:

```
interface/daemon.py
        |
        v
infrastructure/container.py  (AppContainer)
        |
        +---> SchedulerService    (core/domain/services)
        |          |
        |          +--> ISchedulerRepository  (core/ports/outbound)
        |          |         ^
        |          |         |
        |          |    SQLiteSchedulerRepo  (adapters/outbound/scheduler)
        |          |
        |          +--> dispatch ports:
        |                 - ChannelRegistry  (existing)
        |                 - ILLMProvider     (existing, via AgentContainer)
        |                 - asyncio subprocess (stdlib)
        |
        +---> ScheduleTaskUseCase  (core/use_cases)
                   |
                   +--> ISchedulerRepository  (same instance)
```

**Key boundaries**:
- `core/domain/` — entities, enums, `SchedulerService`, errors. No I/O.
- `core/ports/inbound/` — `ISchedulerUseCase` ABC.
- `core/ports/outbound/` — `ISchedulerRepository` Protocol.
- `core/use_cases/` — `ScheduleTaskUseCase`: CRUD + builtin-guard.
- `adapters/outbound/scheduler/` — `SQLiteSchedulerRepo` with `aiosqlite`.
- `infrastructure/config.py` — adds `SchedulerConfig`.
- `infrastructure/container.py` — wires + owns `SchedulerService` lifecycle.

**Dispatch dependency inversion**: `SchedulerService` receives a `SchedulerDispatchPorts` dataclass (just a container) with pre-resolved callables/ports — it does NOT import adapters directly. The container injects real implementations.

---

## 2. Data Model

### 2.1 SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT    NOT NULL,
    task_kind            TEXT    NOT NULL,        -- 'recurrent' | 'oneshot'
    trigger_type         TEXT    NOT NULL,        -- 'channel.send_message' | ...
    trigger_payload      TEXT    NOT NULL,        -- JSON blob
    schedule             TEXT    NOT NULL,        -- cron expr (recurrent) OR ISO datetime (oneshot)
    next_run             REAL    NOT NULL,        -- Unix timestamp (epoch seconds)
    status               TEXT    NOT NULL DEFAULT 'pending',
    executions_remaining INTEGER,                 -- NULL=infinite; only meaningful for recurrent
    retry_count          INTEGER NOT NULL DEFAULT 0,
    enabled              INTEGER NOT NULL DEFAULT 1,
    created_at           REAL    NOT NULL,
    updated_at           REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run
    ON scheduled_tasks(enabled, status, next_run);

CREATE TABLE IF NOT EXISTS task_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    started_at  REAL    NOT NULL,
    finished_at REAL,
    status      TEXT    NOT NULL,                  -- 'completed' | 'failed' | 'missed'
    output      TEXT,
    error       TEXT,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_task_logs_task ON task_logs(task_id, started_at);
```

**Notes on `task_kind`**:
- `recurrent`: `schedule` is a cron expression (e.g. `"0 * * * *"`). `next_run` is computed by croniter. `executions_remaining` can be NULL (infinite) or N (countdown).
- `oneshot`: `schedule` is an ISO 8601 datetime string. `executions_remaining` is always NULL (field is not used). Post-execution the row transitions to `status='completed'`, `enabled=0`.

### 2.2 Trigger Payload Shapes (JSON-serialized in `trigger_payload`)

```python
# channel.send_message
class ChannelSendPayload(BaseModel):
    channel_id: str
    text: str

# agent.send_to_llm
class AgentSendPayload(BaseModel):
    agent_id: str
    prompt_override: str | None = None
    tools_override: list[dict] | None = None
    output_channel: str | None = None   # if None -> store output in task_logs

# shell_exec
class ShellExecPayload(BaseModel):
    command: str
    working_dir: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    timeout: int | None = None          # seconds; if None -> no timeout

# cli_command
class CliCommandPayload(BaseModel):
    args: list[str]
    timeout: int | None = None
```

A discriminated union `TriggerPayload = Annotated[Union[...], Field(discriminator='type')]` is used at the entity level; the repo serializes/deserializes via `model_dump_json()` / `model_validate_json()`.

---

## 3. Domain Entities

File: `core/domain/entities/task.py`

```python
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Literal, Annotated, Union
from pydantic import BaseModel, Field


class TaskKind(str, Enum):
    RECURRENT = "recurrent"
    ONESHOT = "oneshot"


class TriggerType(str, Enum):
    CHANNEL_SEND = "channel.send_message"
    AGENT_SEND = "agent.send_to_llm"
    SHELL_EXEC = "shell_exec"
    CLI_COMMAND = "cli_command"


class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    MISSED    = "missed"
    DISABLED  = "disabled"


# --- typed payloads (discriminated union by 'type') -----------------

class ChannelSendPayload(BaseModel):
    type: Literal["channel.send_message"] = "channel.send_message"
    channel_id: str
    text: str


class AgentSendPayload(BaseModel):
    type: Literal["agent.send_to_llm"] = "agent.send_to_llm"
    agent_id: str
    prompt_override: str | None = None
    tools_override: list[dict] | None = None
    output_channel: str | None = None


class ShellExecPayload(BaseModel):
    type: Literal["shell_exec"] = "shell_exec"
    command: str
    working_dir: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    timeout: int | None = None


class CliCommandPayload(BaseModel):
    type: Literal["cli_command"] = "cli_command"
    args: list[str]
    timeout: int | None = None


TriggerPayload = Annotated[
    Union[ChannelSendPayload, AgentSendPayload, ShellExecPayload, CliCommandPayload],
    Field(discriminator="type"),
]


# --- root entity ----------------------------------------------------

class ScheduledTask(BaseModel):
    id: int
    name: str
    task_kind: TaskKind
    trigger_type: TriggerType
    trigger_payload: TriggerPayload
    schedule: str                          # cron expr OR ISO datetime
    next_run: float                        # epoch seconds
    status: TaskStatus = TaskStatus.PENDING
    executions_remaining: int | None = None
    retry_count: int = 0
    enabled: bool = True
    created_at: float
    updated_at: float

    @property
    def is_builtin(self) -> bool:
        return self.id < 100
```

File: `core/domain/entities/task_log.py`

```python
from pydantic import BaseModel


class TaskLog(BaseModel):
    id: int | None = None
    task_id: int
    started_at: float
    finished_at: float | None = None
    status: str                 # 'completed' | 'failed' | 'missed'
    output: str | None = None
    error: str | None = None
```

File: `core/domain/errors.py` (additions)

```python
class SchedulerError(Exception):                 ...
class BuiltinTaskProtectedError(SchedulerError): ...
class InvalidTriggerTypeError(SchedulerError):   ...
class TaskNotFoundError(SchedulerError):         ...
```

---

## 4. Port Interfaces

### 4.1 Inbound: `ISchedulerUseCase`

File: `core/ports/inbound/scheduler_port.py`

```python
from abc import ABC, abstractmethod
from core.domain.entities.task import ScheduledTask, TaskKind, TriggerType, TriggerPayload


class ISchedulerUseCase(ABC):

    @abstractmethod
    async def create_task(
        self,
        *,
        name: str,
        task_kind: TaskKind,
        trigger_type: TriggerType,
        trigger_payload: TriggerPayload,
        schedule: str,
        executions_remaining: int | None = None,
        enabled: bool = True,
    ) -> ScheduledTask: ...

    @abstractmethod
    async def get_task(self, task_id: int) -> ScheduledTask: ...

    @abstractmethod
    async def list_tasks(self, *, only_enabled: bool = False) -> list[ScheduledTask]: ...

    @abstractmethod
    async def update_task(self, task_id: int, **fields) -> ScheduledTask: ...

    @abstractmethod
    async def delete_task(self, task_id: int) -> None: ...
    """Raises BuiltinTaskProtectedError if task_id < 100."""

    @abstractmethod
    async def enable_task(self, task_id: int) -> ScheduledTask: ...

    @abstractmethod
    async def disable_task(self, task_id: int) -> ScheduledTask: ...
```

### 4.2 Outbound: `ISchedulerRepository` (Protocol)

File: `core/ports/outbound/scheduler_port.py`

```python
from typing import Protocol
from core.domain.entities.task import ScheduledTask
from core.domain.entities.task_log import TaskLog


class ISchedulerRepository(Protocol):

    async def ensure_schema(self) -> None: ...
    async def save_task(self, task: ScheduledTask) -> ScheduledTask: ...
    """INSERT or UPDATE. Assigns id via MAX(id, 99)+1 when id is None/0."""
    async def get_task(self, task_id: int) -> ScheduledTask | None: ...
    async def list_tasks(self, *, only_enabled: bool = False) -> list[ScheduledTask]: ...
    async def delete_task(self, task_id: int) -> None: ...
    async def get_next_due(self) -> ScheduledTask | None: ...
    """Returns task with smallest next_run where enabled=1 AND status='pending'."""
    async def list_due_pending(self, *, as_of: float) -> list[ScheduledTask]: ...
    """All tasks with next_run < as_of, status='pending', enabled=1 — used at startup."""
    async def update_status(self, task_id: int, status: str) -> None: ...
    async def update_after_execution(
        self,
        task_id: int,
        *,
        status: str,
        next_run: float | None,
        executions_remaining: int | None,
        retry_count: int,
        enabled: bool,
    ) -> None: ...
    async def save_log(self, log: TaskLog) -> None: ...
    async def seed_builtin(self, task: ScheduledTask) -> None: ...
    """INSERT OR IGNORE — no-op if row exists."""
```

---

## 5. SchedulerService Design

File: `core/domain/services/scheduler_service.py`

### 5.1 Dispatch ports container

```python
from dataclasses import dataclass
from typing import Protocol, Awaitable, Callable


class ChannelSender(Protocol):
    async def send_message(self, channel_id: str, text: str) -> None: ...


class LLMDispatcher(Protocol):
    async def run_agent_once(
        self,
        agent_id: str,
        prompt_override: str | None,
        tools_override: list[dict] | None,
    ) -> str: ...


@dataclass
class SchedulerDispatchPorts:
    channel_sender: ChannelSender
    llm_dispatcher: LLMDispatcher
    # shell_exec and cli_command use asyncio subprocess directly
```

### 5.2 Service class sketch

```python
import asyncio, heapq, json, time, logging
from croniter import croniter
from datetime import datetime

logger = logging.getLogger(__name__)

_IDLE_SLEEP_MAX = 60.0   # NFR-01


class SchedulerService:
    def __init__(
        self,
        repo: ISchedulerRepository,
        dispatch: SchedulerDispatchPorts,
        config: SchedulerConfig,
    ) -> None:
        self._repo = repo
        self._dispatch = dispatch
        self._config = config
        self._wake = asyncio.Event()       # used to invalidate idle sleep on CRUD mutations
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # ---- lifecycle ----
    async def start(self) -> None:
        await self._repo.ensure_schema()
        await self._handle_missed_on_startup()
        self._task = asyncio.create_task(self._run(), name="scheduler-loop")

    async def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._task:
            await self._task

    def invalidate(self) -> None:
        """Called by use case after any mutation to force loop re-scan."""
        self._wake.set()

    # ---- main loop ----
    async def _run(self) -> None:
        while not self._stopping.is_set():
            next_task = await self._repo.get_next_due()
            now = time.time()

            if next_task is None:
                sleep_for = _IDLE_SLEEP_MAX
            else:
                sleep_for = max(0.0, next_task.next_run - now)

            if sleep_for > 0:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=sleep_for)
                    continue       # woken up -> re-scan
                except asyncio.TimeoutError:
                    pass

            if next_task is None:
                continue

            # Re-fetch in case it was mutated while we slept
            fresh = await self._repo.get_task(next_task.id)
            if fresh is None or fresh.status != TaskStatus.PENDING or not fresh.enabled:
                continue
            if fresh.next_run > time.time():
                continue

            await self._execute_task(fresh)

    # ---- execution ----
    async def _execute_task(self, task: ScheduledTask) -> None:
        await self._repo.update_status(task.id, TaskStatus.RUNNING.value)
        task.retry_count = 0
        last_error: str | None = None
        output: str | None = None
        success = False

        while task.retry_count < self._config.max_retries:
            started = time.time()
            try:
                output = await self._dispatch_trigger(task)
                success = True
                finished = time.time()
                await self._repo.save_log(TaskLog(
                    task_id=task.id,
                    started_at=started,
                    finished_at=finished,
                    status="completed",
                    output=self._truncate_output(output),
                    error=None,
                ))
                break
            except Exception as exc:
                finished = time.time()
                last_error = f"{type(exc).__name__}: {exc}"
                await self._repo.save_log(TaskLog(
                    task_id=task.id,
                    started_at=started,
                    finished_at=finished,
                    status="failed",
                    output=None,
                    error=last_error,
                ))
                task.retry_count += 1
                logger.warning("Task %d attempt %d failed: %s", task.id, task.retry_count, last_error)

        await self._finalize_task(task, success=success)

    def _truncate_output(self, s: str | None) -> str | None:
        if s is None:
            return None
        max_size = self._config.output_truncation_size
        if len(s) <= max_size:
            return s
        return s[:max_size] + f"\n... [truncated, {len(s) - max_size} bytes omitted]"

    # ---- dispatch ----
    async def _dispatch_trigger(self, task: ScheduledTask) -> str | None:
        payload = task.trigger_payload

        if isinstance(payload, ChannelSendPayload):
            await self._dispatch.channel_sender.send_message(payload.channel_id, payload.text)
            return None

        if isinstance(payload, AgentSendPayload):
            output = await self._dispatch.llm_dispatcher.run_agent_once(
                payload.agent_id, payload.prompt_override, payload.tools_override,
            )
            if payload.output_channel:
                await self._dispatch.channel_sender.send_message(payload.output_channel, output)
                return None
            return output           # stored in task_logs.output by caller

        if isinstance(payload, ShellExecPayload):
            return await self._run_shell(payload)

        if isinstance(payload, CliCommandPayload):
            return await self._run_cli(payload)

        raise InvalidTriggerTypeError(f"Unknown payload: {type(payload).__name__}")

    async def _run_shell(self, p: ShellExecPayload) -> str:
        proc = await asyncio.create_subprocess_shell(
            p.command,
            cwd=p.working_dir,
            env={**os.environ, **p.env_vars} if p.env_vars else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=p.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"shell_exec timeout after {p.timeout}s")
        if proc.returncode != 0:
            raise RuntimeError(f"shell_exec exit {proc.returncode}: {stdout.decode(errors='replace')}")
        return stdout.decode(errors="replace")

    async def _run_cli(self, p: CliCommandPayload) -> str:
        proc = await asyncio.create_subprocess_exec(
            *p.args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=p.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"cli_command timeout after {p.timeout}s")
        if proc.returncode != 0:
            raise RuntimeError(f"cli_command exit {proc.returncode}: {stdout.decode(errors='replace')}")
        return stdout.decode(errors="replace")

    # ---- post-execution bookkeeping ----
    async def _finalize_task(self, task: ScheduledTask, *, success: bool) -> None:
        now = time.time()

        if not success:
            await self._repo.update_after_execution(
                task.id,
                status=TaskStatus.FAILED.value,
                next_run=task.next_run,
                executions_remaining=task.executions_remaining,
                retry_count=task.retry_count,
                enabled=task.enabled,
            )
            return

        if task.task_kind == TaskKind.ONESHOT:
            await self._repo.update_after_execution(
                task.id,
                status=TaskStatus.COMPLETED.value,
                next_run=task.next_run,
                executions_remaining=None,
                retry_count=0,
                enabled=False,
            )
            return

        # recurrent
        new_remaining = task.executions_remaining
        if new_remaining is not None:
            new_remaining -= 1

        if new_remaining is not None and new_remaining <= 0:
            await self._repo.update_after_execution(
                task.id,
                status=TaskStatus.COMPLETED.value,
                next_run=task.next_run,
                executions_remaining=0,
                retry_count=0,
                enabled=False,
            )
            return

        next_run = croniter(task.schedule, datetime.fromtimestamp(now)).get_next(float)
        await self._repo.update_after_execution(
            task.id,
            status=TaskStatus.PENDING.value,
            next_run=next_run,
            executions_remaining=new_remaining,
            retry_count=0,
            enabled=True,
        )

    # ---- missed task handling ----
    async def _handle_missed_on_startup(self) -> None:
        now = time.time()
        due = await self._repo.list_due_pending(as_of=now)
        for task in due:
            if task.task_kind == TaskKind.ONESHOT:
                await self._repo.update_after_execution(
                    task.id,
                    status=TaskStatus.MISSED.value,
                    next_run=task.next_run,
                    executions_remaining=None,
                    retry_count=0,
                    enabled=False,
                )
                await self._repo.save_log(TaskLog(
                    task_id=task.id,
                    started_at=now,
                    finished_at=now,
                    status="missed",
                    output=None,
                    error=f"Task missed on restart (next_run={task.next_run}, now={now})",
                ))
            else:
                # recurrent — recompute next_run, do NOT execute past runs
                next_run = croniter(task.schedule, datetime.fromtimestamp(now)).get_next(float)
                await self._repo.update_after_execution(
                    task.id,
                    status=TaskStatus.PENDING.value,
                    next_run=next_run,
                    executions_remaining=task.executions_remaining,
                    retry_count=0,
                    enabled=True,
                )
```

### 5.3 Heap invalidation strategy

Rather than maintaining an in-memory heap that must be synced with DB state, the design uses **DB as source of truth**:
- Each loop iteration calls `get_next_due()` (cheap: indexed scan).
- The `_wake` event is set by the use case after any CRUD mutation (via `SchedulerService.invalidate()`).
- On wake, the loop restarts the scan, guaranteeing freshness.
- This avoids heap resync bugs at the cost of a single indexed SELECT per iteration — acceptable for a single-daemon scheduler with low task cardinality.

The `heapq` mentioned in the spec is the logical order produced by `ORDER BY next_run ASC LIMIT 1` (SQLite uses the index as the "heap").

---

## 6. SQLiteSchedulerRepo Design

File: `adapters/outbound/scheduler/sqlite_scheduler_repo.py`

Pattern mirrors `sqlite_history_store.py` exactly:

```python
class SQLiteSchedulerRepo(ISchedulerRepository):

    def __init__(self, cfg: SchedulerConfig) -> None:
        self._db_path = cfg.db_path
        Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON")
            yield conn

    async def ensure_schema(self) -> None:
        async with self._conn() as conn:
            await conn.executescript(_CREATE_TABLES)
            await conn.commit()

    async def save_task(self, task: ScheduledTask) -> ScheduledTask:
        payload_json = task.trigger_payload.model_dump_json()
        async with self._conn() as conn:
            if task.id == 0:
                # User task insert — allocate id via MAX(id, 99) + 1
                cursor = await conn.execute(
                    """
                    INSERT INTO scheduled_tasks
                        (id, name, task_kind, trigger_type, trigger_payload, schedule,
                         next_run, status, executions_remaining, retry_count, enabled,
                         created_at, updated_at)
                    SELECT COALESCE(MAX(id), 99) + 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    FROM scheduled_tasks
                    WHERE id >= 100
                    """,
                    (task.name, task.task_kind.value, task.trigger_type.value, payload_json,
                     task.schedule, task.next_run, task.status.value, task.executions_remaining,
                     task.retry_count, int(task.enabled), task.created_at, task.updated_at),
                )
                task_id = cursor.lastrowid
                # lastrowid may not reflect the SELECT-based insert — fetch the max explicitly
                row = await conn.execute_fetchall(
                    "SELECT MAX(id) AS id FROM scheduled_tasks WHERE id >= 100"
                )
                task.id = int(row[0]["id"])
            else:
                # Explicit ID (used by seed_builtin and update path)
                await conn.execute(
                    """
                    INSERT OR REPLACE INTO scheduled_tasks (...) VALUES (...)
                    """,
                    (task.id, task.name, ...),
                )
            await conn.commit()
        return task

    async def seed_builtin(self, task: ScheduledTask) -> None:
        payload_json = task.trigger_payload.model_dump_json()
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO scheduled_tasks
                    (id, name, task_kind, trigger_type, trigger_payload, schedule,
                     next_run, status, executions_remaining, retry_count, enabled,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task.id, task.name, task.task_kind.value, task.trigger_type.value,
                 payload_json, task.schedule, task.next_run, task.status.value,
                 task.executions_remaining, task.retry_count, int(task.enabled),
                 task.created_at, task.updated_at),
            )
            await conn.commit()

    async def get_next_due(self) -> ScheduledTask | None:
        async with self._conn() as conn:
            rows = await conn.execute_fetchall(
                """
                SELECT * FROM scheduled_tasks
                WHERE enabled = 1 AND status = 'pending'
                ORDER BY next_run ASC
                LIMIT 1
                """
            )
            return self._row_to_task(rows[0]) if rows else None

    async def list_due_pending(self, *, as_of: float) -> list[ScheduledTask]:
        async with self._conn() as conn:
            rows = await conn.execute_fetchall(
                """
                SELECT * FROM scheduled_tasks
                WHERE enabled = 1 AND status = 'pending' AND next_run < ?
                ORDER BY next_run ASC
                """,
                (as_of,),
            )
            return [self._row_to_task(r) for r in rows]

    async def save_log(self, log: TaskLog) -> None:
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO task_logs (task_id, started_at, finished_at, status, output, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (log.task_id, log.started_at, log.finished_at, log.status, log.output, log.error),
            )
            await conn.commit()

    async def update_after_execution(self, task_id: int, *, status, next_run,
                                     executions_remaining, retry_count, enabled) -> None:
        async with self._conn() as conn:
            await conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = ?, next_run = ?, executions_remaining = ?,
                    retry_count = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, next_run, executions_remaining, retry_count,
                 int(enabled), time.time(), task_id),
            )
            await conn.commit()

    def _row_to_task(self, row: aiosqlite.Row) -> ScheduledTask:
        payload = self._parse_payload(row["trigger_type"], row["trigger_payload"])
        return ScheduledTask(
            id=row["id"],
            name=row["name"],
            task_kind=TaskKind(row["task_kind"]),
            trigger_type=TriggerType(row["trigger_type"]),
            trigger_payload=payload,
            schedule=row["schedule"],
            next_run=row["next_run"],
            status=TaskStatus(row["status"]),
            executions_remaining=row["executions_remaining"],
            retry_count=row["retry_count"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _parse_payload(self, trigger_type: str, raw_json: str) -> TriggerPayload:
        data = json.loads(raw_json)
        data["type"] = trigger_type          # ensure discriminator present
        # Use pydantic TypeAdapter for discriminated union
        return _PAYLOAD_ADAPTER.validate_python(data)
```

Where `_PAYLOAD_ADAPTER = TypeAdapter(TriggerPayload)`.

### 6.1 ID allocation note

The spec requires user IDs `>= 100`. Because the schema does NOT use `AUTOINCREMENT` on `scheduled_tasks` (`id INTEGER PRIMARY KEY` so SQLite would otherwise assign the lowest free rowid, colliding with 1..99), we MUST use the explicit `SELECT COALESCE(MAX(id), 99) + 1 WHERE id >= 100` pattern inside the INSERT. Builtin rows (id < 100) are inserted with their explicit ID via `seed_builtin()` and never interfere.

---

## 7. SchedulerConfig

File: `infrastructure/config.py` (addition)

```python
class SchedulerConfig(BaseModel):
    enabled: bool = True
    db_path: str = "data/scheduler.db"
    max_retries: int = 3
    output_truncation_size: int = 65536   # 64 KB — NFR: task_logs.output bound


class GlobalConfig(BaseModel):
    app: AppConfig
    llm: LLMConfig
    embedding: EmbeddingConfig
    memory: MemoryConfig
    history: HistoryConfig
    scheduler: SchedulerConfig = SchedulerConfig()    # NEW
    skills: SkillsConfig = SkillsConfig()
    tools: ToolsConfig = ToolsConfig()
```

And in `load_global_config` add `scheduler = SchedulerConfig(**merged.get("scheduler", {}))` and pass it into `GlobalConfig(...)`.

Note: the spec mentions TOML, but the current config system reads YAML (`config/global.yaml`). We keep the existing YAML stack — the `[scheduler]` naming from spec maps to the `scheduler:` YAML section. This is consistent with how `memory`, `history` already work. If the user later switches to TOML, the Pydantic model is unchanged.

---

## 8. Builtin Task Seed

File: `core/domain/services/scheduler_service.py` (bootstrap helper) OR `infrastructure/container.py`.

```python
BUILTIN_CONSOLIDATE_MEMORY = ScheduledTask(
    id=1,
    name="consolidate_memory",
    task_kind=TaskKind.RECURRENT,
    trigger_type=TriggerType.CLI_COMMAND,
    trigger_payload=CliCommandPayload(
        args=["python", "-m", "interface.cli", "memory", "consolidate"],
        timeout=600,
    ),
    schedule="0 3 * * *",                  # 03:00 daily
    next_run=_compute_first_next_run("0 3 * * *"),
    status=TaskStatus.PENDING,
    executions_remaining=None,             # infinite
    retry_count=0,
    enabled=True,
    created_at=time.time(),
    updated_at=time.time(),
)
```

Seeded via `repo.seed_builtin(BUILTIN_CONSOLIDATE_MEMORY)` during `SchedulerService.start()` BEFORE `_handle_missed_on_startup`. Because it uses `INSERT OR IGNORE`, re-running is a no-op.

**Missing at dispatch time**: the main loop calls `get_task(1)` only when it's at the top of the queue. If deleted out-of-band (direct SQL), `get_next_due()` simply won't return it. The spec scenario "builtin missing at dispatch" is handled by the loop's re-fetch check: `fresh = await self._repo.get_task(next_task.id); if fresh is None: continue; logger.error(...)`. We do NOT recreate it.

---

## 9. Container Wiring

File: `infrastructure/container.py` (modifications to `AppContainer`)

```python
class AppContainer:
    def __init__(self, global_config: GlobalConfig, registry: AgentRegistry) -> None:
        self.global_config = global_config
        self.registry = registry
        self.agents: dict[str, AgentContainer] = {}

        for agent_cfg in registry.list_all():
            try:
                self.agents[agent_cfg.id] = AgentContainer(agent_cfg, global_config)
            except Exception as exc:
                logger.error("Error creating container for '%s': %s", agent_cfg.id, exc)

        # --- scheduler wiring ---
        self.scheduler_repo = SQLiteSchedulerRepo(global_config.scheduler)
        self.schedule_task_uc = ScheduleTaskUseCase(
            repo=self.scheduler_repo,
            on_mutation=self._on_scheduler_mutation,     # forwards to service.invalidate()
        )

        dispatch_ports = SchedulerDispatchPorts(
            channel_sender=ChannelSenderAdapter(self),   # thin adapter over ChannelRegistry
            llm_dispatcher=LLMDispatcherAdapter(self),   # thin adapter over AgentContainer.run_agent
        )
        self.scheduler_service = SchedulerService(
            repo=self.scheduler_repo,
            dispatch=dispatch_ports,
            config=global_config.scheduler,
        )

    def _on_scheduler_mutation(self) -> None:
        self.scheduler_service.invalidate()

    async def startup(self) -> None:
        """Called by daemon entry point."""
        if self.global_config.scheduler.enabled:
            await self.scheduler_service.start()

    async def shutdown(self) -> None:
        await self.scheduler_service.stop()
```

`LLMDispatcherAdapter` looks up `self.get_agent(agent_id).run_agent.run(prompt)` and returns the string output. `ChannelSenderAdapter` routes to the appropriate channel adapter based on `channel_id` prefix.

**Daemon lifecycle hook**: `interface/daemon.py` calls `await app_container.startup()` after construction and `await app_container.shutdown()` on signal.

---

## 10. Testing Strategy

Test file locations:
- `tests/unit/domain/test_scheduler_service.py`
- `tests/unit/use_cases/test_schedule_task.py`
- `tests/integration/scheduler/test_sqlite_scheduler_repo.py`
- `tests/integration/scheduler/test_scheduler_end_to_end.py`

Fixtures (in `tests/conftest.py` or `tests/integration/scheduler/conftest.py`):

```python
@pytest.fixture
async def scheduler_repo():
    cfg = SchedulerConfig(db_path=":memory:")
    # :memory: needs a single connection; wrap aiosqlite.connect manually
    repo = SQLiteSchedulerRepo(cfg)
    await repo.ensure_schema()
    yield repo
```

Note: `:memory:` is per-connection in SQLite. Either (a) use `"file::memory:?cache=shared"` with a URI flag, or (b) use a `tmp_path` file fixture — cleaner and faster. The existing `sqlite_history_store.py` likely uses tmp_path; mirror that.

### Key scenarios

| Test | Approach |
|---|---|
| Schema bootstrap idempotent | Call `ensure_schema()` twice, assert no error |
| Builtin guard | Create task id=1 via `seed_builtin`, call `delete_task(1)` via UC, assert `BuiltinTaskProtectedError`, assert row still present |
| User ID allocation | Seed builtin id=1, create 3 user tasks, assert ids 100, 101, 102 |
| Oneshot completes | Create oneshot task, freeze time past next_run, run one loop iteration, assert status=completed, enabled=0 |
| Recurrent recompute | Create recurrent "* * * * *", freeze time, run iteration, assert next_run advanced by 1 minute, status back to pending |
| Countdown | Create recurrent with executions_remaining=2, run 2 iterations, assert completed after 2nd |
| Retry exhaustion | Mock dispatch port to always raise, max_retries=3, run task, assert 3 task_logs entries with status=failed, final task status=failed |
| Retry success on 2nd | Mock raises once then returns, assert 1 failed log + 1 completed log, final status=pending (recurrent) |
| Missed oneshot on restart | Insert oneshot with next_run in past, call `start()`, assert status=missed + log entry |
| Missed recurrent recompute | Insert recurrent with next_run 3h in past, call `start()`, assert next_run advanced, no past runs executed |
| shell_exec timeout | Use `sleep 10` command with timeout=1, assert task fails with timeout error in log |
| agent.send_to_llm no channel | Mock LLM returns "hello", no output_channel, assert task_logs.output="hello" |
| Output truncation | Mock LLM returns 100KB string, assert task_logs.output is 64KB + truncation marker |

### Time freezing

Use `freezegun` (via `pytest-freezegun`) for deterministic `croniter` tests:

```python
from freezegun import freeze_time

@freeze_time("2026-01-01 12:00:00")
async def test_recurrent_next_run_computed(scheduler_repo):
    ...
```

For loop tests, avoid real sleeps: inject a fake clock + patch `asyncio.wait_for` timeout to 0, or drive the loop one iteration at a time by exposing a `_run_once()` helper (pragmatic).

---

## 11. File Map

| File | Action | Description |
|---|---|---|
| `core/domain/entities/task.py` | Modified | Replace old UUID entity with int-ID `ScheduledTask`, enums, discriminated payloads |
| `core/domain/entities/task_log.py` | New | `TaskLog` Pydantic model |
| `core/domain/errors.py` | Modified | Add `SchedulerError`, `BuiltinTaskProtectedError`, `InvalidTriggerTypeError`, `TaskNotFoundError` |
| `core/domain/services/scheduler_service.py` | New | `SchedulerService` async loop + dispatch + missed-task handling |
| `core/ports/inbound/scheduler_port.py` | Modified | `ISchedulerUseCase` ABC with int IDs + CRUD |
| `core/ports/outbound/scheduler_port.py` | New | `ISchedulerRepository` Protocol |
| `core/use_cases/schedule_task.py` | Modified | Replace JSON backend with repo + builtin guard + mutation hook |
| `adapters/outbound/scheduler/__init__.py` | New | Package marker |
| `adapters/outbound/scheduler/sqlite_scheduler_repo.py` | New | aiosqlite adapter mirroring `sqlite_history_store.py` |
| `adapters/outbound/scheduler/builtin_tasks.py` | New | `BUILTIN_CONSOLIDATE_MEMORY` definition |
| `adapters/outbound/scheduler/dispatch_adapters.py` | New | `ChannelSenderAdapter`, `LLMDispatcherAdapter` thin wrappers |
| `infrastructure/config.py` | Modified | Add `SchedulerConfig`, wire into `GlobalConfig` + `load_global_config` |
| `infrastructure/container.py` | Modified | Wire `SQLiteSchedulerRepo`, `SchedulerService`, `startup()` / `shutdown()` |
| `interface/daemon.py` | Modified | Call `container.startup()` and `container.shutdown()` around main loop |
| `config/global.yaml` | Modified | Add `scheduler:` section with defaults |
| `tests/unit/domain/test_scheduler_service.py` | New | Unit tests with mocked repo + dispatch |
| `tests/unit/use_cases/test_schedule_task.py` | New | UC tests (builtin guard, ID allocation) |
| `tests/integration/scheduler/test_sqlite_scheduler_repo.py` | New | Repo round-trip tests against real SQLite (tmp_path) |
| `tests/integration/scheduler/test_scheduler_end_to_end.py` | New | Full loop tests with freezegun |
| `pyproject.toml` | Modified | Add `croniter` and (optional) `pytest-freezegun` dev dep |

---

## Open Questions / Tradeoffs

1. **DB-as-source-of-truth vs in-memory heap**: chosen DB for correctness; cost is one indexed SELECT per iteration. Acceptable at expected cardinality (tens/hundreds of tasks).
2. **`:memory:` SQLite in tests**: use `tmp_path` files instead — SQLite `:memory:` is per-connection and our adapter opens a fresh connection per call. This matches how `sqlite_history_store.py` is tested.
3. **LLMDispatcher abstraction**: we introduce a narrow `run_agent_once(agent_id, prompt_override, tools_override)` port rather than reusing `RunAgentUseCase` directly — keeps `SchedulerService` decoupled from the full agent use case surface. The adapter in `dispatch_adapters.py` bridges the gap.
4. **`output_truncation_size` location**: placed in `SchedulerConfig` per the spec amendment. Consider making it channel-aware later if different triggers need different budgets.
5. **retry_count persistence**: we reset `retry_count` to 0 at the start of each cycle and persist it only at finalize time. This means mid-cycle crashes lose partial retry progress — acceptable because the task simply retries from 0 on next cycle boundary.
