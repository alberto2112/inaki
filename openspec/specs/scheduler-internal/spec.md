# Scheduler-Internal Specification

## Purpose

Persistent, async-driven cron-style task scheduler integrated into the daemon lifecycle. Supports four typed trigger types, integer IDs with builtin guard, countdown execution model, retry logic, missed-task handling on restart, and structured per-execution logging.

---

## Requirements

### Requirement: SQLite Schema Bootstrap (FR-01)

The system MUST create and maintain the following tables on startup if they do not exist:

- `scheduled_tasks`: `id INTEGER PRIMARY KEY`, `name TEXT`, `trigger_type TEXT`, `trigger_payload TEXT` (JSON), `cron_expr TEXT` (nullable), `next_run REAL` (Unix timestamp), `status TEXT`, `executions_remaining INTEGER` (nullable), `retry_count INTEGER DEFAULT 0`, `enabled INTEGER DEFAULT 1`, `created_at REAL`, `updated_at REAL`
- `task_logs`: `id INTEGER PRIMARY KEY AUTOINCREMENT`, `task_id INTEGER`, `started_at REAL`, `finished_at REAL`, `status TEXT`, `output TEXT`, `error TEXT`

The adapter MUST use `aiosqlite` with `@asynccontextmanager`. Bootstrap MUST be idempotent (`CREATE TABLE IF NOT EXISTS`).

#### Scenario: First startup — tables absent

- GIVEN the database file does not exist
- WHEN the SQLite adapter initializes
- THEN both tables are created and the schema matches the definition above

#### Scenario: Repeated startup — tables exist

- GIVEN the database already has both tables
- WHEN the adapter initializes again
- THEN no error is raised and existing data is preserved

---

### Requirement: Integer ID System and Builtin Guard (FR-02)

Tasks with `id < 100` are system (builtin) tasks. The system MUST NOT allow deletion of any task with `id < 100` via any use-case method. User tasks MUST receive IDs `>= 100` via `MAX(id, 99) + 1` on insert.

#### Scenario: Delete builtin task

- GIVEN a task exists with `id = 1`
- WHEN `delete_task(id=1)` is called on the use case
- THEN `BuiltinTaskProtectedError` is raised
- AND the task remains in the database

#### Scenario: User task deletion allowed

- GIVEN a task exists with `id = 150`
- WHEN `delete_task(id=150)` is called
- THEN the task is removed from the database

#### Scenario: New user task ID allocation

- GIVEN the highest existing task ID is `105`
- WHEN a new user task is created
- THEN the new task receives `id = 106`

---

### Requirement: Typed Trigger Payloads (FR-03)

The system MUST support exactly four trigger types. Each MUST carry a typed JSON payload stored in `trigger_payload`.

| Trigger Type | Required Payload Fields |
|---|---|
| `channel.send_message` | `channel_id: str`, `message: str` |
| `agent.send_to_llm` | `prompt: str`, `output_channel: str \| null` |
| `shell_exec` | `command: str`, `timeout_seconds: int` |
| `cli_command` | `command: str`, `timeout_seconds: int` |

#### Scenario: Valid channel.send_message payload accepted

- GIVEN a task is created with `trigger_type = "channel.send_message"` and payload `{"channel_id": "c1", "message": "hello"}`
- WHEN the task is persisted
- THEN the payload is stored as valid JSON and retrievable intact

#### Scenario: Unknown trigger type rejected

- GIVEN a task creation request with `trigger_type = "unknown_type"`
- WHEN the use case processes the request
- THEN `InvalidTriggerTypeError` is raised and no record is inserted

---

### Requirement: Executions Remaining Countdown (FR-04)

The system MUST implement the following countdown semantics:

- `executions_remaining = NULL` → task runs indefinitely
- `executions_remaining = N > 0` → task runs N more times; decremented after each successful execution
- `executions_remaining = 0` → task is completed; MUST NOT be dispatched

The system MUST NOT allow `executions_remaining` to go below 0. When the countdown reaches 0, the task status MUST transition to `completed`.

#### Scenario: Oneshot task completes after one execution

- GIVEN a task has `executions_remaining = 1`
- WHEN the task executes successfully
- THEN `executions_remaining` is decremented to `0`
- AND `status` transitions to `completed`
- AND the task is not scheduled for future execution

#### Scenario: Infinite task remains active

- GIVEN a task has `executions_remaining = NULL`
- WHEN the task executes successfully
- THEN `executions_remaining` remains `NULL`
- AND the task remains in `pending` status (or `next_run` is recomputed for recurrent)

---

### Requirement: Task Status Transitions (FR-05)

Valid statuses: `pending`, `running`, `completed`, `failed`, `missed`, `disabled`.

Allowed transitions:

| From | To | Trigger |
|---|---|---|
| `pending` | `running` | Dispatch begins |
| `running` | `completed` | Execution succeeds, `executions_remaining` reaches 0 or task is oneshot |
| `running` | `pending` | Execution succeeds, recurrent task, `next_run` recomputed |
| `running` | `failed` | Execution fails after `max_retries` exhausted |
| `pending` | `missed` | Daemon restart detects past `next_run` for oneshot task |
| `pending` / `disabled` | `disabled` / `pending` | `enable_task` / `disable_task` called |

#### Scenario: Task transitions pending → running → pending (recurrent)

- GIVEN a recurrent task with `cron_expr = "* * * * *"` and `status = pending`
- WHEN the scheduler dispatches it and execution succeeds
- THEN `status` returns to `pending`
- AND `next_run` is set to the next cron-computed timestamp

---

### Requirement: Async Scheduler Service Loop (FR-06)

The `SchedulerService` MUST run as a persistent async loop. It MUST use a `heapq` ordered by `next_run` to select the next due task. When no active tasks exist, the loop MUST sleep up to `60` seconds before rechecking. The loop MUST NOT spin (busy-wait). All dispatch MUST be awaited; the loop MUST NOT block the event loop.

#### Scenario: Loop idles with no active tasks

- GIVEN no tasks with `status = pending` and `enabled = 1` exist
- WHEN the scheduler loop iterates
- THEN it sleeps for up to 60 seconds before rechecking
- AND CPU usage remains negligible

#### Scenario: Loop wakes and dispatches due task

- GIVEN a task has `next_run` in the past and `status = pending`
- WHEN the scheduler loop runs
- THEN the task is dispatched immediately
- AND `status` is set to `running` before dispatch begins

---

### Requirement: Trigger Dispatch (FR-07)

The system MUST dispatch each trigger type as follows:

- `channel.send_message`: calls `channel.send_message(channel_id, message)`
- `agent.send_to_llm`: calls the LLM with the given `prompt`; if `output_channel` is set, sends result there; otherwise stores output in `task_logs.output`
- `shell_exec`: runs the command via `asyncio.create_subprocess_shell`; enforces `timeout_seconds` via `asyncio.wait_for`
- `cli_command`: runs the command via subprocess; enforces `timeout_seconds` via `asyncio.wait_for`

All dispatch calls MUST be async/awaited. Timeout violations MUST result in task `status = failed` and the error recorded in `task_logs.error`.

#### Scenario: agent.send_to_llm with no output_channel

- GIVEN a task with `trigger_type = "agent.send_to_llm"` and `output_channel = null`
- WHEN the task executes successfully
- THEN the LLM response is stored in `task_logs.output`
- AND no channel message is sent

#### Scenario: shell_exec with timeout exceeded

- GIVEN a task with `trigger_type = "shell_exec"` and `timeout_seconds = 5`
- WHEN the command runs longer than 5 seconds
- THEN `asyncio.wait_for` cancels the subprocess
- AND `status` is set to `failed`
- AND `task_logs.error` records a timeout message

---

### Requirement: Missed Task Handling on Restart (FR-08)

On daemon startup, the system MUST scan all tasks with `status = pending` and `next_run < now()`:

- Oneshot tasks (`executions_remaining = 1` or any finite non-recurrent): MUST set `status = missed`; MUST NOT execute them.
- Recurrent tasks (`cron_expr IS NOT NULL`): MUST recompute `next_run` to the next future occurrence via croniter; MUST NOT mark as missed; MUST NOT execute past runs.

#### Scenario: Oneshot task missed on restart

- GIVEN a oneshot task with `next_run` 2 hours in the past and `status = pending`
- WHEN the daemon starts
- THEN `status` is set to `missed`
- AND the task is not dispatched
- AND a log entry records the missed execution

#### Scenario: Recurrent task recomputed on restart

- GIVEN a recurrent task with `cron_expr = "0 * * * *"` and `next_run` 3 hours in the past
- WHEN the daemon starts
- THEN `next_run` is updated to the next future cron occurrence
- AND `status` remains `pending`
- AND no missed entry is created

---

### Requirement: Retry Logic (FR-09)

On execution failure, the system MUST increment `retry_count`. If `retry_count < max_retries` (from `SchedulerConfig`), the task MUST be re-queued for immediate retry. When `retry_count >= max_retries`, `status` MUST transition to `failed`. `retry_count` MUST reset to `0` at the start of each new execution cycle (not across cycles).

#### Scenario: Task retries and eventually fails

- GIVEN `max_retries = 3` and a task that always fails
- WHEN the task is dispatched
- THEN it is retried up to 3 times (`retry_count` reaches 3)
- AND `status` transitions to `failed`
- AND a `task_logs` entry exists for each attempt

#### Scenario: Task retries and succeeds on second attempt

- GIVEN `max_retries = 3` and a task that fails once then succeeds
- WHEN the task is dispatched
- THEN `retry_count = 1` after the first failure
- AND on the second attempt the task succeeds
- AND `retry_count` resets to `0` on the next cycle

---

### Requirement: Task Execution Logging (FR-10)

The system MUST write one `task_logs` record per execution attempt, containing: `task_id`, `started_at`, `finished_at`, `status` (`completed` / `failed`), `output` (truncated if oversized), `error` (if applicable). Writes MUST be atomic (single transaction per attempt).

#### Scenario: Successful execution logged

- GIVEN a task executes successfully
- WHEN the execution finishes
- THEN one row is inserted in `task_logs` with `status = completed`, `finished_at` set, and `error = null`

#### Scenario: Failed execution logged with error

- GIVEN a task raises an exception during dispatch
- WHEN the exception is caught
- THEN one row is inserted in `task_logs` with `status = failed` and `error` containing the exception message

---

### Requirement: Builtin Task Seed (FR-11)

On startup the system MUST seed builtin task ID 1 (`consolidate_memory`) using `INSERT OR IGNORE`. The task MUST use `trigger_type = "cli_command"` with an appropriate command payload. If the builtin task record is absent from the DB when invoked by the loop, the system MUST log an error and MUST NOT attempt to recreate it; the loop MUST continue.

#### Scenario: Builtin task seeded on first startup

- GIVEN the database is empty
- WHEN the daemon starts
- THEN task ID 1 (`consolidate_memory`) exists in `scheduled_tasks`

#### Scenario: Builtin task seed idempotent

- GIVEN task ID 1 already exists
- WHEN the daemon starts again
- THEN no duplicate row is inserted and the existing task is unchanged

#### Scenario: Builtin task missing at dispatch time

- GIVEN task ID 1 has been manually deleted directly from the database
- WHEN the scheduler loop tries to dispatch it
- THEN an error is logged
- AND the loop continues processing other tasks without crashing

---

### Requirement: SchedulerConfig (FR-12)

The system MUST expose a `SchedulerConfig` Pydantic model with fields: `max_retries: int` (default 3), `enabled: bool` (default True), `db_path: str`. Config MUST be loaded from the TOML configuration file under the `[scheduler]` section.

#### Scenario: Config loaded from TOML

- GIVEN `config.toml` contains `[scheduler]` with `max_retries = 5` and `db_path = "data/scheduler.db"`
- WHEN `SchedulerConfig` is instantiated from that section
- THEN `max_retries = 5` and `db_path = "data/scheduler.db"`

#### Scenario: Config defaults applied when section absent

- GIVEN `config.toml` does not contain a `[scheduler]` section
- WHEN `SchedulerConfig` is instantiated with no arguments
- THEN `max_retries = 3` and `enabled = True`

---

## Non-Functional Requirements

### NFR-01: Loop Non-Spinning

The scheduler loop MUST NOT busy-wait. It MUST compute sleep duration as `max(0, next_run - now())` and sleep that duration, capped at 60 seconds when no tasks are due.

### NFR-02: ACID Writes

All database mutations (status transitions, log inserts, countdown decrements) MUST be wrapped in a single `aiosqlite` transaction. Partial state on failure is not acceptable.

### NFR-03: Timeout Enforcement

`shell_exec` and `cli_command` dispatch MUST enforce `timeout_seconds` via `asyncio.wait_for`. Uncaught timeouts MUST be recorded as failures; the loop MUST continue.

### NFR-04: Testability

All components (use case, service, repository) MUST be testable with an in-memory SQLite database (`:memory:`). The `SchedulerService` MUST accept injected dependencies so dispatch ports can be mocked in tests.

---

## Invariants

1. Tasks with `id < 100` MUST NOT be deletable via any use-case method — `BuiltinTaskProtectedError` is raised unconditionally.
2. `executions_remaining` MUST NOT go below `0` — set to `0` and transition to `completed`.
3. `retry_count` MUST reset to `0` at the start of each new execution cycle.
4. A builtin task missing from the DB at dispatch time → log error, do NOT recreate, return error; loop continues.
5. `SchedulerService` MUST NOT block the event loop — all dispatch is async/awaited.
6. `task_logs.output` MUST be truncated if the payload exceeds a defined max size (e.g., 64 KB) to prevent unbounded storage growth.
