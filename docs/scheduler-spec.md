# Scheduler — Technical Specification

## Table of Contents

1. [Purpose and architecture](#1-purpose-and-architecture)
2. [Use cases](#2-use-cases)
3. [Execution flow](#3-execution-flow)
4. [CLI commands](#4-cli-commands)
5. [Trigger types](#5-trigger-types)
6. [Action types (TaskKind)](#6-action-types-taskkind)
7. [Domain models](#7-domain-models)
8. [Configuration](#8-configuration)
9. [Builtin tasks](#9-builtin-tasks)
10. [Error handling](#10-error-handling)
11. [SQLite schema](#11-sqlite-schema)
12. [Layer architecture](#12-layer-architecture)

---

## 1. Purpose and architecture

The scheduler is a background task execution engine that runs continuously within the daemon's lifecycle. It provides:

- **Agent dispatching** with custom prompts and tools at defined schedules
- **Message sending** to channels (Telegram, etc.) on a programmed basis
- **Shell command execution** with timeout and environment control
- **Memory consolidation** for all enabled agents periodically
- **Flexible scheduling**: cron expressions for recurring tasks, ISO datetime for one-shot tasks
- **Execution tracking**: logs with status, output, errors, and retry counter
- **Task lifecycle**: `PENDING → RUNNING → [COMPLETED | FAILED | MISSED]`

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Daemon / AppContainer                │
│                                                         │
│  ┌──────────────┐    ┌─────────────────────────────┐    │
│  │  CLI (Typer) │    │      SchedulerService       │    │
│  │  list/show/  │    │   (async event loop)        │    │
│  │  edit/enable │    │                             │    │
│  └──────┬───────┘    └──────────────┬──────────────┘    │
│         │                           │                   │
│  ┌──────▼───────────────────────────▼───────────────┐   │
│  │           ScheduleTaskUseCase                    │   │
│  │     (CRUD + on_mutation → invalidate)            │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │           SQLiteSchedulerRepo                    │   │
│  │        data/scheduler.db                         │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  SchedulerDispatchPorts:                                │
│    ChannelSenderAdapter  →  Telegram / other gateways   │
│    LLMDispatcherAdapter  →  AgentContainer.run_agent    │
│    ConsolidationAdapter  →  ConsolidateAllAgentsUC      │
└─────────────────────────────────────────────────────────┘
```

---

## 2. Use cases

### 2.1 Daily memory consolidation

Builtin case. The scheduler runs memory consolidation for all enabled agents according to the cron configured in `memory.schedule` (default: `0 3 * * *` = 3 AM user-local every day).

**Trigger**: `consolidate_memory`  
**Frequency**: recurring, configurable cron  
**Protection**: Fixed ID `1`, cannot be deleted or accidentally overwritten

### 2.2 Periodic report to Telegram

Send a daily/weekly message to a Telegram channel. Example: agent activity summary, reminder, status notification.

```yaml
trigger_type: channel_send
trigger_payload:
  type: channel_send
  target: "telegram:123456789"
  text: "Buenos días. Este es el resumen del día."
schedule: "0 9 * * 1-5"   # Monday to Friday 9 AM (user-local)
task_kind: recurrent
```

### 2.3 Shell script execution

Run a Python or bash script on a schedule. The scheduler captures stdout and stores it in the logs.

```yaml
trigger_type: shell_exec
trigger_payload:
  type: shell_exec
  command: "python scripts/cleanup.py"
  working_dir: "/home/user/project"
  timeout: 120
task_kind: recurrent
schedule: "0 2 * * 0"   # Sundays 2 AM
```

### 2.4 Scheduled agent dispatch

Launch an agent with a specific prompt on a schedule. The result can be sent to a channel or stored in the logs.

```yaml
trigger_type: agent_send
trigger_payload:
  type: agent_send
  agent_id: "analyst"
  task: "Genera el reporte semanal de actividad."
  output_channel: "telegram:987654321"
task_kind: recurrent
schedule: "0 8 * * 1"   # Monday 8 AM
```

### 2.5 Scheduled one-shot task

Execute a task exactly once at a specific date/time. Afterwards it transitions to `COMPLETED`.

```yaml
task_kind: oneshot
schedule: "2025-12-31T23:59:00+00:00"
trigger_type: channel_send
trigger_payload:
  type: channel_send
  target: "telegram:123456789"
  text: "¡Feliz año nuevo!"
```

### 2.6 Recurring task with execution limit

Execute N times and then automatically complete.

```yaml
task_kind: recurrent
schedule: "0 10 * * *"
executions_remaining: 5    # runs 5 times, then COMPLETED
trigger_type: agent_send
trigger_payload:
  type: agent_send
  agent_id: "onboarding-bot"
  task: "Continúa el onboarding del usuario."
```

---

## 3. Execution flow

### 3.1 Main loop (`SchedulerService._loop()`)

```
while running:
    now = utcnow()
    task = repo.get_next_due()
               └─ earliest PENDING + enabled task (next_run IS NOT NULL)
               └─ skips stale payloads (ValidationError → warning, continues)

    if task is None:
        wait 60s  ──or──  until invalidate() event fires
        continue

    if task.next_run > now:
        wait = min((next_run - now).total_seconds(), 60)
        wait ──or──  until invalidate() event fires
        continue

    _execute_task(task)
```

**`invalidate()`** is called by `ScheduleTaskUseCase` every time a CRUD operation occurs (create, update, delete, enable, disable). This wakes the loop immediately to re-evaluate without waiting for the 60s timeout.

### 3.2 Task execution (`_execute_task()`)

```
task.status → RUNNING

for attempt in range(0, max_retries + 1):
    try:
        output = _dispatch_trigger(task)
        _finalize_task(task, output)
        break

    except Exception as e:
        log warning (attempt N)
        task.retry_count = attempt + 1
        if log_enabled:
            save TaskLog(status="failed", error=str(e))
        sleep(retry_backoff_seconds * (attempt + 1))   # backoff lineal entre intentos

else:
    # all retries exhausted
    if ONESHOT:
        task.status → FAILED                            # terminal
    else:  # RECURRENT
        # El fallo de UNA ocurrencia no mata la recurrencia: avanza al
        # próximo slot del cron y vuelve a PENDING. Los intentos fallidos
        # quedan en task_logs.
        next_run = next_cron_occurrence(schedule, user_tz)
        repo.update_after_execution(next_run=next_run, retry_count=0, last_run=None)
```

### 3.3 Dispatch by trigger type

| Trigger | Action |
|---------|--------|
| `channel_send` | `channel_router.send_message(target, text)` → `DispatchResult(original_target, resolved_target)` — the router applies a fallback cascade (native → override → default → hardcoded `~/.inaki/data/scheduler-fallback.log`). See [configuracion.md — `channel_fallback`](configuracion.md#scheduler--channel_fallback-routing-de-canales). The `{original_target, resolved_target}` pair is persisted in `task_logs.metadata` (JSON) for traceability. |
| `agent_send` | `llm_dispatcher.dispatch(agent_id, prompt, tools)` → str result; if `output_channel` is defined, sends result to the channel |
| `shell_exec` | `ShellExecAdapter` (port `IShellExecutor`): subprocess with command/working_dir/env_vars/timeout → stdout; RuntimeError if exit code != 0. On timeout the process is **killed** (no orphans). |
| `consolidate_memory` | `consolidator.consolidate_all()` → str result |

### 3.4 Finalization (`_finalize_task()`)

```
output = truncate(output, config.output_truncation_size)   # default 65536 bytes

if log_enabled:
    save TaskLog(status="success", output=output)

if task_kind == ONESHOT:
    task.status → COMPLETED

else:  # RECURRENT
    if executions_remaining is not None:
        remaining = executions_remaining - 1
    else:
        remaining = None

    if remaining == 0:
        task.status → COMPLETED
    else:
        next_run = next_cron_occurrence(schedule, user_tz)   # SIEMPRE en tz del usuario
        repo.update_after_execution(
            next_run=next_run,
            executions_remaining=remaining,
            retry_count=0,         ← reset
            last_run=now           ← solo se escribe cuando hubo ejecución real
        )
        task.status remains PENDING
```

> **Timezone**: toda evaluación de cron (repo, service, reconciliadores de
> builtins) pasa por `core/domain/utils/cron.py::next_cron_occurrence()`, que
> interpreta la expresión en la timezone del usuario (`user.timezone`) y
> devuelve UTC. Evaluar cron en más de un lugar con tz distintas causó el bug
> histórico de doble ejecución separada por el offset DST (6:00 local + 6:00 UTC).

### 3.5 Startup recovery (`_recover_on_startup()`)

On service startup, two situations are repaired:

**1. Tasks stuck in RUNNING** (the daemon died mid-execution):

```
for task in repo.list_running():
    save TaskLog(status="failed", error="Daemon restarted while task was running")
    if ONESHOT:
        task.status → FAILED
    else:  # RECURRENT
        next_run = next_cron_occurrence(schedule, user_tz)   # back to PENDING
```

**2. Tasks that should have run while the daemon was stopped:**

```
tasks = repo.list_due_pending(now)
         └─ PENDING + enabled + next_run <= now

for task in tasks:
    save TaskLog(status="missed", error="Task was not running when...")
    if ONESHOT:
        task.status → MISSED
    else:  # RECURRENT
        # Skip the missed execution, recalculate next date.
        # last_run is NOT touched — nothing actually ran.
        next_run = next_cron_occurrence(schedule, user_tz)
        repo.update_after_execution(next_run=next_run, last_run=None)
```

Skipped occurrences (oneshot **and** recurrent) always leave a `"missed"`
entry in `task_logs` (respecting `log_enabled`), so "why didn't yesterday's
6 AM summary arrive?" is answerable from the logs.

Missed recurring tasks **are not re-executed**; they advance to the next cron slot. Missed one-shot tasks remain as `MISSED`.

---

## 4. CLI commands

All commands are under the `inaki scheduler` subcommand.

### `inaki scheduler list`

Lists all tasks in a formatted table.

```
inaki scheduler list [--json] [--enabled-only]
```

| Option | Description |
|--------|-------------|
| `--json` | JSON output with all fields |
| `--enabled-only` | Filters only enabled tasks (`enabled = true`) |

**Columns**: ID, Name, Kind, Trigger, Enabled, Next execution

---

### `inaki scheduler show <ID>`

Shows full detail of a task.

```
inaki scheduler show <ID> [--json]
```

| Option | Description |
|--------|-------------|
| `--json` | Full JSON output (model dump) |

Without `--json` it shows YAML with all fields: id, name, description, task_kind, trigger_type, trigger_payload, schedule, enabled, status, retry_count, executions_remaining, log_enabled, created_at, last_run, next_run.

---

### `inaki scheduler edit <ID>`

Interactive editing in `$EDITOR` via YAML round-trip.

```
inaki scheduler edit <ID>
```

- Opens the editor with **editable fields** in YAML
- Validates the Pydantic schema on save; up to 3 attempts
- Prints `"Task <ID> updated."` on confirmation

**Editable fields**:
```
name, description, task_kind, trigger_type, trigger_payload,
schedule, enabled, executions_remaining, log_enabled
```

**Non-editable fields** (managed by the runtime):
```
id, status, next_run, last_run, created_at, retry_count
```

> **Important**: when changing `trigger_type`, also update `trigger_payload.type` with the same value — it is a discriminated union.

---

### `inaki scheduler enable <ID>`

Sets `enabled = true`. If the task was `FAILED`/`MISSED`, it is also re-armed
(status → `PENDING`, retry_count → 0, next_run recomputed).

```
inaki scheduler enable <ID>
```

---

### `inaki scheduler disable <ID>`

Sets `enabled = false` without touching runtime status. The loop skips it.

```
inaki scheduler disable <ID>
```

---

### `inaki scheduler rm <ID>`

Deletes a task from the database.

```
inaki scheduler rm <ID>
```

> **Protection**: tasks with `id < 100` are builtin and cannot be deleted. Raises `BuiltinTaskProtectedError`.

---

## 5. Trigger types

Trigger types determine what the scheduler does when a task fires.

### `channel_send`

Sends a text message to a channel.

```python
class ChannelSendPayload(BaseModel):
    type: Literal["channel_send"] = "channel_send"
    target: str    # format: "telegram:<user_id>" (prefix:destination)
    text: str          # message text
```

**Example**:
```json
{
  "type": "channel_send",
  "target": "telegram:123456789",
  "text": "Recordatorio: reunión en 30 minutos."
}
```

---

### `agent_send`

Dispatches an agent with a prompt and optional tools. The result can be redirected to a channel.

```python
class AgentSendPayload(BaseModel):
    type: Literal["agent_send"] = "agent_send"
    agent_id: str                           # ID of the agent to dispatch
    task: str                               # Message/task to send to the agent
    tools_override: list[dict] | None = None  # Available tools; None = all
    output_channel: str | None = None       # If defined, sends result to the channel
```

**Example**:
```json
{
  "type": "agent_send",
  "agent_id": "analyst",
  "task": "Genera el reporte semanal.",
  "output_channel": "telegram:123456789"
}
```

---

### `shell_exec`

Executes a shell command. Captures stdout. Fails if exit code != 0.

```python
class ShellExecPayload(BaseModel):
    type: Literal["shell_exec"] = "shell_exec"
    command: str                     # Command to execute
    working_dir: str | None = None   # Working directory; None = current cwd
    env_vars: dict[str, str] = {}    # Additional environment variables
    timeout: int | None = None       # Timeout in seconds; None = use config (default 300)
```

**Example**:
```json
{
  "type": "shell_exec",
  "command": "python scripts/cleanup.py --dry-run",
  "working_dir": "/home/user/project",
  "env_vars": {"ENV": "production"},
  "timeout": 120
}
```

---

### `consolidate_memory`

Runs memory consolidation for all enabled agents. Requires no parameters.

```python
class ConsolidateMemoryPayload(BaseModel):
    type: Literal["consolidate_memory"] = "consolidate_memory"
    # No fields — the consolidator reads the registry at runtime
```

**Example**:
```json
{
  "type": "consolidate_memory"
}
```

---

## 6. Action types (TaskKind)

### `recurrent`

The task repeats according to a cron expression. After each execution, the next `next_run` is recalculated.

- `schedule`: standard cron expression (5 fields) — **evaluated in the user's
  timezone** (`user.timezone`, with DST), not UTC. `0 6 * * *` means 06:00
  local time every day, year-round.
- `executions_remaining`: `null` = infinite; `N` = execute N times then transition to `COMPLETED`
  (failed occurrences do not consume the countdown)

**Cron examples**:

| Expression | Meaning |
|-----------|---------|
| `0 3 * * *` | Every day at 3:00 AM (user-local) |
| `0 9 * * 1-5` | Monday to Friday, 9:00 AM (user-local) |
| `*/15 * * * *` | Every 15 minutes |
| `0 0 1 * *` | First day of each month, midnight (user-local) |

---

### `oneshot`

The task executes exactly once at a specific date/time, then transitions to `COMPLETED`.

- `schedule`: ISO datetime with timezone (e.g. `"2025-06-01T10:00:00+00:00"`)
- If the daemon was not running at the scheduled time, the task remains as `MISSED`

---

## 7. Domain models

### `ScheduledTask`

Main entity. File: [core/domain/entities/task.py](../core/domain/entities/task.py)

```python
class ScheduledTask(BaseModel):
    id: int = 0                              # 0 = unassigned; repo assigns on save; id<100 = builtin
    name: str                                # Descriptive name
    description: str = ""                   # Optional description
    task_kind: TaskKind                      # RECURRENT | ONESHOT
    trigger_type: TriggerType                # Trigger type
    trigger_payload: TriggerPayload          # Payload (discriminated union by "type")
    schedule: str                            # Cron or ISO datetime
    enabled: bool = True                     # False = skipped by the loop
    executions_remaining: int | None = None  # RECURRENT only: None=∞, N=countdown
    status: TaskStatus = PENDING
    retry_count: int = 0                     # Retry counter for the current attempt
    log_enabled: bool = True                 # If True, saves TaskLog per execution
    created_at: datetime                     # UTC
    last_run: datetime | None = None         # Last successful execution
    next_run: datetime | None = None         # Next scheduled execution (UTC)
```

---

### `TaskStatus`

```python
class TaskStatus(str, Enum):
    """Runtime execution state. Orthogonal to the `enabled` flag (user intent).
    The loop filters by `enabled=1 AND status='pending'`: both are required."""
    PENDING   = "pending"    # Waiting for next execution
    RUNNING   = "running"    # Currently executing
    COMPLETED = "completed"  # Finished (oneshot completed or countdown=0)
    FAILED    = "failed"     # ONESHOT only: exhausted retries (recurrent tasks advance instead)
    MISSED    = "missed"     # Oneshot that did not execute (daemon was stopped)
```

> **Note**: the `disabled` value was removed because it mixed two orthogonal
> dimensions (user intent + runtime state). The intent of "I don't want this
> to run" is modeled solely via `ScheduledTask.enabled=False`.

---

### `TaskLog`

Record of each execution. File: [core/domain/entities/task_log.py](../core/domain/entities/task_log.py)

```python
class TaskLog(BaseModel):
    id: int = 0
    task_id: int                     # FK → scheduled_tasks.id
    started_at: datetime
    finished_at: datetime | None = None
    status: str                      # "success" | "failed" | "missed"
    output: str | None = None        # captured stdout (truncated to output_truncation_size)
    error: str | None = None         # Exception message if failed
```

---

## 8. Configuration

Global configuration file: `~/.inaki/config/global.yaml`

### `scheduler` block

```yaml
scheduler:
  enabled: true                    # Enable/disable the scheduler on startup
  db_filename: "data/scheduler.db" # SQLite file relative to ~/.inaki/ (or absolute)
  max_retries: 3                   # Maximum retries per failed task
  retry_backoff_seconds: 10.0      # Linear wait between retries (1x, 2x, 3x...)
  max_tasks_per_agent: 20          # Active (pending/running) tasks an agent may own
  output_truncation_size: 65536    # Maximum bytes to store in task_logs.output
```

Cron expressions are evaluated in `user.timezone` (empty → UTC fallback).

### `memory` block (affects builtin task)

```yaml
memory:
  schedule: "0 3 * * *"    # Cron for the builtin consolidate_memory task
  delay_seconds: 2          # Pause between agents during consolidation
```

If `memory.schedule` changes, the scheduler detects the change on startup and automatically updates the builtin task (ID 1).

---

## 9. Builtin tasks

Builtin tasks have `id < 100` and are **protected**: they cannot be deleted or overwritten via normal CRUD.

### ID 1 — `consolidate_memory`

```
id:           1
name:         consolidate_memory
description:  Global memory consolidation (all enabled agents)
task_kind:    RECURRENT
trigger_type: consolidate_memory
schedule:     configurable via memory.schedule (default: "0 3 * * *")
executions_remaining: null (infinite)
```

**Reconciliation on startup** (`AppContainer._reconcile_consolidate_memory_task`):

1. Reads `memory.schedule` from config
2. Queries the task in the DB
3. If it doesn't exist → creates it (`seed_builtin`)
4. If the schedule changed → updates + recalculates `next_run`
5. If `status == FAILED` → resets to `PENDING`
6. If `next_run == NULL` → recalculates
7. If the payload is corrupt (`ValidationError`) → deletes and re-creates clean

---

## 10. Error handling

File: [core/domain/errors.py](../core/domain/errors.py)

| Error | When raised |
|-------|-------------|
| `TaskNotFoundError` | `get_task(id)` with non-existent ID |
| `BuiltinTaskProtectedError` | Attempt to modify/delete a task with `id < USER_TASK_ID_START` (100) |
| `InvalidTriggerTypeError` | Payload with unknown trigger type in dispatch |
| `InvalidScheduleError` | Malformed cron expression or unparseable ISO datetime |
| `TooManyActiveTasksError` | Agent exceeded `scheduler.max_tasks_per_agent` |
| `SchedulerError` | Base for all scheduler errors |

### Retries

- Configurable: `scheduler.max_retries` (default: 3) with linear backoff
  between attempts (`scheduler.retry_backoff_seconds`, default 10s → 10/20/30s)
- The retry_count resets to 0 on each successful execution
- ONESHOT with retries exhausted → status `FAILED`; reactivate with
  `inaki scheduler enable <ID>` (re-arms and recomputes next_run)
- RECURRENT with retries exhausted → advances to the next cron slot and stays
  `PENDING` — one failed occurrence does not kill the recurrence

---

## 11. SQLite schema

Database: `~/.inaki/data/scheduler.db` (or the path configured in `scheduler.db_filename`)

### `scheduled_tasks` table

```sql
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id                    INTEGER PRIMARY KEY,
    name                  TEXT NOT NULL,
    description           TEXT NOT NULL DEFAULT '',
    task_kind             TEXT NOT NULL,           -- "recurrent" | "oneshot"
    trigger_type          TEXT NOT NULL,           -- see TriggerType enum
    trigger_payload       TEXT NOT NULL,           -- JSON with "type" discriminator field
    schedule              TEXT NOT NULL,           -- cron or ISO datetime
    next_run              REAL,                    -- UNIX timestamp (float UTC), NULL = never calculated
    status                TEXT NOT NULL DEFAULT 'pending',
    enabled               INTEGER NOT NULL DEFAULT 1,  -- 0=false, 1=true
    executions_remaining  INTEGER,                 -- NULL or countdown
    retry_count           INTEGER NOT NULL DEFAULT 0,
    log_enabled           INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL,           -- ISO string UTC
    last_run              TEXT                     -- ISO string UTC or NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_due
    ON scheduled_tasks(enabled, status, next_run);
```

### `task_logs` table

```sql
CREATE TABLE IF NOT EXISTS task_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES scheduled_tasks(id),
    started_at  TEXT NOT NULL,     -- ISO string UTC
    finished_at TEXT,              -- ISO string UTC or NULL
    status      TEXT NOT NULL,     -- "success" | "failed" | "missed"
    output      TEXT,              -- captured stdout (truncated)
    error       TEXT               -- exception message if failed
);
```

> `next_run` is stored as a UNIX timestamp (`REAL`) to allow efficient comparisons with `WHERE next_run <= ?`. All other dates are stored as ISO strings.

---

## 12. Layer architecture

### Key files

| Component | File | Class/Function |
|-----------|------|----------------|
| CLI | [inaki/scheduler_cli.py](../inaki/scheduler_cli.py) | `scheduler_app` (Typer), commands: `list_cmd`, `show_cmd`, `edit_cmd`, `enable_cmd`, `disable_cmd`, `rm_cmd` |
| Use Case | [core/use_cases/schedule_task.py](../core/use_cases/schedule_task.py) | `ScheduleTaskUseCase`, `ISchedulerUseCase` |
| Service | [core/domain/services/scheduler_service.py](../core/domain/services/scheduler_service.py) | `SchedulerService` |
| Entities | [core/domain/entities/task.py](../core/domain/entities/task.py) | `ScheduledTask`, `TaskKind`, `TriggerType`, `TaskStatus`, payloads |
| Task logs | [core/domain/entities/task_log.py](../core/domain/entities/task_log.py) | `TaskLog` |
| Inbound port | [core/ports/inbound/scheduler_port.py](../core/ports/inbound/scheduler_port.py) | `ISchedulerUseCase` |
| Outbound port | [core/ports/outbound/scheduler_port.py](../core/ports/outbound/scheduler_port.py) | `ISchedulerRepository` (Protocol) |
| Repository | [adapters/outbound/scheduler/sqlite_scheduler_repo.py](../adapters/outbound/scheduler/sqlite_scheduler_repo.py) | `SQLiteSchedulerRepo` |
| Dispatch adapters | [adapters/outbound/scheduler/dispatch_adapters.py](../adapters/outbound/scheduler/dispatch_adapters.py) | `ChannelRouter`, `LLMDispatcherAdapter`, `ConsolidationDispatchAdapter`, `HttpCallerAdapter`, `SchedulerDispatchPorts` |
| Outbound sinks | [adapters/outbound/sinks/](../adapters/outbound/sinks/) | `TelegramSink`, `FileSink`, `NullSink`, `SinkFactory` (port: `core/ports/outbound/outbound_sink_port.py::IOutboundSink`) |
| Value objects | [core/domain/value_objects/dispatch_result.py](../core/domain/value_objects/dispatch_result.py) | `DispatchResult(original_target, resolved_target)` |
| Builtin tasks | [adapters/outbound/scheduler/builtin_tasks.py](../adapters/outbound/scheduler/builtin_tasks.py) | `build_consolidate_memory_task()`, `CONSOLIDATE_MEMORY_TASK_ID` |
| Config | [infrastructure/config.py](../infrastructure/config.py) | `SchedulerConfig`, `GlobalConfig` |
| DI Container | [infrastructure/container.py](../infrastructure/container.py) | `AppContainer` |
| Errors | [core/domain/errors.py](../core/domain/errors.py) | `SchedulerError`, `BuiltinTaskProtectedError`, `InvalidTriggerTypeError`, `TaskNotFoundError` |

### Dependency flow

```
CLI ──► ScheduleTaskUseCase ──► ISchedulerRepository
                │                      │
                │ on_mutation()         │ SQLiteSchedulerRepo
                ▼                      ▼
        SchedulerService          scheduler.db
                │
                ▼
        SchedulerDispatchPorts
         ├── ChannelSenderAdapter   → TelegramGateway (etc.)
         ├── LLMDispatcherAdapter   → AgentContainer.run_agent
         └── ConsolidationAdapter  → ConsolidateAllAgentsUseCase
```

### Wiring in AppContainer

```python
# infrastructure/container.py
scheduler_repo = SQLiteSchedulerRepo(config.scheduler.db_filename)

schedule_task_uc = ScheduleTaskUseCase(
    repo=scheduler_repo,
    on_mutation=lambda: scheduler_service.invalidate(),
)

dispatch_ports = SchedulerDispatchPorts(
    channel_sender=ChannelSenderAdapter(self),
    llm_dispatcher=LLMDispatcherAdapter(self.agents),
    consolidator=ConsolidationDispatchAdapter(self.consolidate_all_agents),
)

scheduler_service = SchedulerService(
    repo=scheduler_repo,
    dispatch=dispatch_ports,
    config=config.scheduler,
)

# Lifecycle
await startup():  reconcile_builtin() + scheduler_service.start()
await shutdown(): scheduler_service.stop()
```
