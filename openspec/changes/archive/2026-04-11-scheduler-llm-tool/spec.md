# Spec: scheduler-llm-tool

**Change**: scheduler-llm-tool  
**Branch**: feat/scheduler-llm-tool  
**Status**: draft  
**Depends on**: proposal.md  

---

## Overview

This spec defines the functional requirements and behavioural scenarios for exposing Inaki's built-in scheduler to the LLM as a single multi-operation tool (`SchedulerTool`). All requirements trace to the decisions captured in the proposal and pre-decisions artifacts. Scenarios are written in BDD-style Given/When/Then and are the primary acceptance gate for the `sdd-verify` phase.

---

## Requirements

### REQ-ST-1 — SchedulerTool registration and availability

**Description**: A `SchedulerTool` instance must be created for every `AgentContainer` and registered in that agent's tool executor so that the LLM can invoke it through the standard tool loop.

**Acceptance criteria**:
1. `SchedulerTool` exists at `adapters/outbound/tools/scheduler_tool.py` and implements `ITool` (from `core/ports/outbound/tool_port.py`).
2. `SchedulerTool.name` equals `"scheduler"`.
3. The tool is registered in the agent's `ToolExecutor` after `AgentContainer.wire_scheduler()` is called.
4. Before `wire_scheduler()` is called, the tool is NOT registered (two-phase wiring guarantee).
5. If the system scheduler is disabled, `wire_scheduler()` is a no-op and no tool is registered.
6. `SchedulerTool.parameters_schema` is a valid JSON Schema object that documents `operation`, all per-operation required/optional fields, and the dual time format for `schedule`.

---

### REQ-ST-2 — Create operation

**Description**: The `create` operation creates a new scheduled task with full authorship tracking, trigger type selection, and dual time format support.

**Acceptance criteria**:
1. Required parameters: `operation="create"`, `name: str`, `task_kind: str`, `trigger_type: str`, `trigger_payload: dict`, `schedule: str`.
2. Optional parameters: `description: str`, `executions_remaining: int`.
3. `trigger_type` must be one of `channel_send`, `agent_send`, `shell_exec`. Any other value returns `ToolResult(success=False)`.
4. `trigger_payload` is validated against the corresponding Pydantic model. Invalid payload returns `ToolResult(success=False)` with the validation error message.
5. `schedule` accepts two formats:
   - **Relative** (`schedule[0] == '+'`): parsed by the tool as `+Xd`, `+Xh`, `+Xm`, or combinations (`+2d3h5m`). Converted to an absolute ISO 8601 datetime by adding the offset to `datetime.utcnow()`. Invalid relative format returns `ToolResult(success=False)`.
   - **ISO 8601** (fallback, `schedule[0] != '+'`): passed directly to the use case. Invalid datetime string returns `ToolResult(success=False)`.
   - For recurring tasks (`task_kind == "recurring"`), `schedule` is treated as a cron expression regardless of the `+` discriminator (cron expressions never start with `+`).
6. `created_by` is set to the `agent_id` bound at construction time — never accepted from the LLM caller.
7. On success, returns `ToolResult(success=True)` with the created task's ID and name.
8. Guardrail: if the active task count for `agent_id` is >= 21, returns `ToolResult(success=False)` with `TooManyActiveTasksError` message before attempting creation (see REQ-ST-7).

---

### REQ-ST-3 — List operation

**Description**: The `list` operation returns all scheduled tasks visible to the LLM, regardless of which agent created them.

**Acceptance criteria**:
1. Required parameters: `operation="list"`. No additional required parameters.
2. Returns `ToolResult(success=True)` with a serialised list of all tasks (id, name, description, status, schedule, trigger_type, created_by).
3. If no tasks exist, returns `ToolResult(success=True)` with an empty list — not an error.
4. In v1, no per-agent filtering — all agents see all tasks. This is intentional (see proposal Out of Scope).
5. Optional parameter `status: str` MAY be added for future filtering but is NOT required in v1.

---

### REQ-ST-4 — Get operation

**Description**: The `get` operation retrieves a single task by ID.

**Acceptance criteria**:
1. Required parameters: `operation="get"`, `task_id: int`.
2. Returns `ToolResult(success=True)` with the full task serialisation on success.
3. If the task does not exist, returns `ToolResult(success=False)` with a `TaskNotFoundError` message.
4. No authorship check — any agent can get any task.

---

### REQ-ST-5 — Update operation

**Description**: The `update` operation modifies mutable fields on an existing task. Builtin tasks (id < 100) are protected.

**Acceptance criteria**:
1. Required parameters: `operation="update"`, `task_id: int`. At least one mutable field must be provided.
2. Mutable fields: `name`, `description`, `schedule`, `trigger_payload`, `executions_remaining`, `status`.
3. Immutable fields (`id`, `created_by`, `task_kind`) are ignored if provided — they are silently dropped, NOT an error.
4. If `task_id < 100`, returns `ToolResult(success=False)` with `BuiltinTaskProtectedError` message.
5. If the task does not exist, returns `ToolResult(success=False)` with a `TaskNotFoundError` message.
6. Dual time format applies to `schedule` field updates — same parsing rules as REQ-ST-2 AC5.
7. On success, returns `ToolResult(success=True)` with updated task ID and name.

---

### REQ-ST-6 — Delete operation

**Description**: The `delete` operation removes a task permanently. Builtin tasks (id < 100) are protected.

**Acceptance criteria**:
1. Required parameters: `operation="delete"`, `task_id: int`.
2. If `task_id < 100`, returns `ToolResult(success=False)` with `BuiltinTaskProtectedError` message.
3. If the task does not exist, returns `ToolResult(success=False)` with a `TaskNotFoundError` message.
4. On success, returns `ToolResult(success=True)` with confirmation message including task ID.
5. No authorship check — any agent can delete any non-builtin task (consistent with proposal decision).

---

### REQ-ST-7 — Guardrail: max 21 active tasks per agent

**Description**: Each agent is limited to 21 simultaneously active tasks of its own authorship. This prevents runaway task spawning.

**Acceptance criteria**:
1. "Active" is defined as tasks where `status NOT IN ('completed', 'failed')` AND `created_by == requesting_agent_id`.
2. Before persisting a new task, `ScheduleTaskUseCase.create_task` calls `ISchedulerRepository.count_active_by_agent(agent_id)`.
3. If the count is >= 21, the use case raises `TooManyActiveTasksError` (from `core/domain/errors.py`).
4. `SchedulerTool.create` catches `TooManyActiveTasksError` and returns `ToolResult(success=False, error=<message>)`.
5. Tasks created via CLI (with `created_by == ""`) do NOT count toward any agent's cap.
6. The cap is per-agent, not system-wide — two agents can each have 21 active tasks simultaneously.
7. `ISchedulerRepository` defines `count_active_by_agent(agent_id: str) -> int`. The SQLite implementation uses `SELECT COUNT(*) FROM scheduled_tasks WHERE created_by = ? AND status NOT IN ('completed', 'failed')`.

---

### REQ-ST-8 — Relative time parsing

**Description**: The tool's internal relative-time parser converts `+Xd`, `+Xh`, `+Xm` strings (and combinations) to absolute UTC datetimes.

**Acceptance criteria**:
1. Discriminator: if `schedule[0] == '+'`, treat as relative; otherwise treat as ISO 8601 or cron expression.
2. Supported units: `d` (days), `h` (hours), `m` (minutes). All three optional, any order, e.g. `+2d`, `+5h`, `+30m`, `+2d3h`, `+1d0h30m`.
3. `+0m` (and any relative offset resolving to zero duration) is REJECTED at the tool layer with `ToolResult(success=False)`. "Execute now" is not a valid scheduled task — input validation, not business logic.
4. Large values (e.g., `+999d`) are valid — no upper bound enforced at parse time. The use case or dispatch layer may enforce horizon limits in future, but NOT in this change.
5. Invalid relative format (e.g., `+5x`, `+`, `+ 2h`) returns a parse error → `ToolResult(success=False)`.
6. The parsed absolute datetime is in ISO 8601 UTC format before being passed to `ScheduleTaskUseCase`.
7. Parsing is handled inside `SchedulerTool` — the use case receives only absolute datetimes or cron expressions. The use case has no knowledge of the relative format.

---

### REQ-ST-9 — Two-phase wiring

**Description**: `ScheduleTaskUseCase` is an `AppContainer` singleton and is not available when `AgentContainer` instances are first constructed. Wiring must be deferred to a second pass.

**Acceptance criteria**:
1. `AgentContainer` exposes a `wire_scheduler(schedule_task_uc: ScheduleTaskUseCase, user_timezone: str) -> None` method.
2. `wire_scheduler()` creates a `SchedulerTool` instance (binding `self.agent_id`, `schedule_task_uc`, and `user_timezone`) and registers it in `self.tool_executor`.
3. `AppContainer` calls `wire_scheduler()` on all `AgentContainer` instances in a second pass, AFTER all containers are built — the same ordering as `wire_delegation()`.
4. `wire_scheduler()` is idempotent: calling it twice does NOT register the tool twice. An internal `_scheduler_wired: bool` flag (or equivalent) prevents double-registration.
5. If `schedule_task_uc` is `None` (scheduler disabled), `wire_scheduler()` is a no-op.

---

### REQ-ST-10 — Error handling

**Description**: All error paths return a valid `ToolResult` to the LLM. No exception escapes the tool boundary into the tool loop.

**Acceptance criteria**:
1. `SchedulerTool.execute()` wraps all use case calls in try/except.
2. Known domain errors (`TaskNotFoundError`, `BuiltinTaskProtectedError`, `TooManyActiveTasksError`) are caught and converted to `ToolResult(success=False, error=str(e))`.
3. Unknown operation string (not in `create|list|get|update|delete`) returns `ToolResult(success=False, error="Unknown operation '{op}'. Valid operations: create, list, get, update, delete.")`.
4. Any unexpected exception is caught by a broad `except Exception as e` fallback and returned as `ToolResult(success=False, error=f"Internal error: {str(e)}")`.
5. No `raise` statements escape `execute()`.
6. This matches the error contract of all other tools in Inaki (consistent behaviour for the tool loop and LLM).

---

### REQ-ST-11 — user.timezone config

**Description**: A `user.timezone` configuration field is added to `GlobalConfig` to surface the user's local timezone. This supports the ISO 8601 fallback path and future system prompt injection.

**Acceptance criteria**:
1. New `UserConfig` Pydantic model with `timezone: str = "UTC"` is added to `infrastructure/config.py`.
2. `GlobalConfig` gains a `user: UserConfig = UserConfig()` field.
3. The YAML key is `user.timezone` (nested under `user:`).
4. `config/global.example.yaml` is updated with a commented example: `# user:\n#   timezone: "America/Argentina/Buenos_Aires"`.
5. `user_timezone` is passed to `AgentContainer.wire_scheduler()` so the `SchedulerTool` can use it internally for ISO 8601 parsing context.
6. The tool description MUST NOT reference `user_timezone` or current datetime — surfacing temporal context to the LLM is NOT the tool's responsibility. If needed, it belongs in the agent's system prompt (separate concern, out of scope).
7. `user_timezone` is stored for internal use by `parse_schedule` (ISO 8601 fallback path) — it is NOT exposed to the LLM in any form.

---

### REQ-ST-12 — created_by field and SQLite migration

**Description**: `ScheduledTask` gains a `created_by` field to track authorship. The SQLite schema is migrated backward-compatibly.

**Acceptance criteria**:
1. `ScheduledTask` in `core/domain/entities/task.py` gains `created_by: str = ""`.
2. The default value of `""` represents "created via CLI or unknown origin".
3. SQLite migration: `ALTER TABLE scheduled_tasks ADD COLUMN created_by TEXT DEFAULT ''`. Executed at startup if the column does not already exist.
4. Migration is idempotent: if `created_by` column already exists, the `ALTER TABLE` is silently swallowed (wrap in `try/except`, catching the "duplicate column" error).
5. Existing rows with `created_by = ""` are unaffected by the guardrail — they do NOT count against any agent's cap (guardrail filters by exact `agent_id` match).
6. `ISchedulerRepository` is updated to persist and retrieve `created_by` in all relevant methods.

---

## Scenarios

### SC-ST-1 — Create one-shot task with relative time

```
Given an agent with agent_id="agent-alpha" that has 0 active tasks
And the SchedulerTool is wired and registered
When the LLM calls scheduler with:
  operation="create"
  name="check email"
  task_kind="one_shot"
  trigger_type="shell_exec"
  trigger_payload={"command": "check_email.sh"}
  schedule="+2h"
Then the tool internally computes: execute_at = utcnow() + timedelta(hours=2)
And calls ScheduleTaskUseCase.create_task with created_by="agent-alpha" and the computed absolute datetime
And returns ToolResult(success=True) with the new task ID
```

---

### SC-ST-2 — Create one-shot task with ISO 8601

```
Given an agent with agent_id="agent-beta" that has 0 active tasks
And the SchedulerTool is wired and registered
When the LLM calls scheduler with:
  operation="create"
  name="market research"
  task_kind="one_shot"
  trigger_type="agent_send"
  trigger_payload={"target_agent": "researcher", "message": "run market analysis"}
  schedule="2026-04-12T02:00:00-03:00"
Then the tool recognises schedule[0] != '+' and treats it as ISO 8601
And calls ScheduleTaskUseCase.create_task with the raw ISO 8601 string
And returns ToolResult(success=True) with the new task ID
```

---

### SC-ST-3 — Create recurring task with cron expression

```
Given an agent with agent_id="agent-gamma" that has 0 active tasks
When the LLM calls scheduler with:
  operation="create"
  name="daily digest"
  task_kind="recurring"
  trigger_type="channel_send"
  trigger_payload={"channel": "general", "message": "Daily digest ready"}
  schedule="0 8 * * *"
Then the tool passes schedule as a cron expression to the use case (no relative parsing)
And returns ToolResult(success=True) with the new task ID
```

---

### SC-ST-4 — Create task exceeds 21-cap

```
Given an agent with agent_id="agent-delta" that already has 21 active tasks (created_by="agent-delta")
When the LLM calls scheduler with operation="create" and any valid parameters
Then ScheduleTaskUseCase.create_task counts 21 active tasks for "agent-delta"
And raises TooManyActiveTasksError
And SchedulerTool catches the error
And returns ToolResult(success=False, error="Agent agent-delta has reached the maximum of 21 active tasks")
And no task is persisted
```

---

### SC-ST-5 — Delete builtin task

```
Given a builtin task with id=10 (system-level, created at boot)
When the LLM calls scheduler with:
  operation="delete"
  task_id=10
Then ScheduleTaskUseCase.delete_task raises BuiltinTaskProtectedError (id < 100)
And SchedulerTool returns ToolResult(success=False, error="Task 10 is a builtin task and cannot be modified or deleted")
And the task is NOT deleted
```

---

### SC-ST-6 — List all tasks

```
Given 3 tasks in the scheduler: task-100 (agent-alpha), task-101 (agent-beta), task-102 (CLI, created_by="")
When the LLM calls scheduler with:
  operation="list"
Then returns ToolResult(success=True) with a list of all 3 tasks
And each task entry includes: id, name, status, schedule, trigger_type, created_by
And no filtering by agent_id is applied (all tasks visible)
```

---

### SC-ST-7 — Update task schedule

```
Given a task with id=150, schedule="0 8 * * *", status="active", created by "agent-alpha"
When the LLM calls scheduler with:
  operation="update"
  task_id=150
  schedule="+3h"
Then the tool parses "+3h" to an absolute UTC datetime (utcnow() + 3h)
And calls ScheduleTaskUseCase.update_task with the new absolute datetime
And returns ToolResult(success=True) with task id=150
```

---

### SC-ST-8 — Invalid operation

```
Given the SchedulerTool is registered
When the LLM calls scheduler with:
  operation="cancel"
Then the tool does NOT call the use case
And returns ToolResult(success=False, error="Unknown operation 'cancel'. Valid operations: create, list, get, update, delete.")
```

---

### SC-ST-9 — Relative time parsing edge cases

```
Case A — +0m (zero offset):
  Given schedule="+0m"
  When the tool parses the value
  Then execute_at = utcnow() (no offset added)
  And the task is created successfully (the use case decides whether to reject past/immediate times)

Case B — large offset (+999d):
  Given schedule="+999d"
  When the tool parses the value
  Then execute_at = utcnow() + timedelta(days=999)
  And no parse error is raised (upper bound NOT enforced at tool level)
  And the task is created successfully

Case C — invalid format (+5x):
  Given schedule="+5x"
  When the tool attempts to parse the value
  Then a parse error is raised internally
  And SchedulerTool returns ToolResult(success=False, error="Invalid relative schedule '+5x'. Use format: +Xd, +Xh, +Xm or combinations (e.g. +2d3h30m).")

Case D — combined units (+1d2h30m):
  Given schedule="+1d2h30m"
  When the tool parses the value
  Then execute_at = utcnow() + timedelta(days=1, hours=2, minutes=30)
  And the task is created successfully
```

---

### SC-ST-10 — wire_scheduler idempotency

```
Given an AgentContainer with agent_id="agent-alpha"
And wire_scheduler() has already been called once
When wire_scheduler() is called a second time (e.g. accidental double-init)
Then the SchedulerTool is NOT registered a second time in the tool executor
And only ONE "scheduler" tool entry exists in the tool executor
And no error or exception is raised
```

---

## Traceability Matrix

| Requirement | Scenarios |
|-------------|-----------|
| REQ-ST-1 | SC-ST-10 |
| REQ-ST-2 | SC-ST-1, SC-ST-2, SC-ST-3, SC-ST-4 |
| REQ-ST-3 | SC-ST-6 |
| REQ-ST-4 | — (covered by SC-ST-5 not-found path implicitly) |
| REQ-ST-5 | SC-ST-7 |
| REQ-ST-6 | SC-ST-5 |
| REQ-ST-7 | SC-ST-4 |
| REQ-ST-8 | SC-ST-1, SC-ST-7, SC-ST-9 |
| REQ-ST-9 | SC-ST-10 |
| REQ-ST-10 | SC-ST-5, SC-ST-8 |
| REQ-ST-11 | — (config only; no behavioral scenario needed) |
| REQ-ST-12 | SC-ST-1, SC-ST-4, SC-ST-6 |

---

## Resolved Questions

1. **Cron + relative disambiguation**: RESOLVED — If `task_kind == "recurring"` and `schedule[0] == '+'`, the tool returns `ToolResult(success=False)` with message "Recurring tasks require a cron expression, not a relative time offset". Invalid combination by definition.

2. **`+0m` semantics**: RESOLVED — Tool rejects `+0m` and any relative offset resolving to zero duration. Input validation in the tool layer. "Execute now" is not a valid scheduled task.

3. **List serialisation format**: RESOLVED — List returns `{"tasks": [...], "total": N}`. Each task object: `id`, `name`, `task_kind`, `status`, `next_run_at`, `trigger_type`, `created_by`. Minimal shape for LLM decision-making. Full `trigger_payload` only via `get` operation.
