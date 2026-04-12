# Tasks: scheduler-llm-tool

## Phase 1: Domain Layer

- [x] 1.1 — Add `created_by: str = ""` to `ScheduledTask`
  - **Files**: `core/domain/entities/task.py`
  - **Requirements**: REQ-ST-12
  - **Description**: Add `created_by: str = ""` field to the `ScheduledTask` entity. Default `""` represents CLI/unknown origin. No other logic changes.
  - **Depends on**: none

- [x] 1.2 — Add `TooManyActiveTasksError` to errors.py
  - **Files**: `core/domain/errors.py`
  - **Requirements**: REQ-ST-7
  - **Description**: Add `TooManyActiveTasksError(SchedulerError)` with message template `"Agent {agent_id} has reached the maximum of 21 active tasks"`.
  - **Depends on**: none

- [x] 1.3 — Create `core/domain/utils/time_parser.py`
  - **Files**: `core/domain/utils/__init__.py` (new), `core/domain/utils/time_parser.py` (new)
  - **Requirements**: REQ-ST-8
  - **Description**: Implement `parse_schedule(raw: str, user_timezone: str) -> datetime`. Relative path: regex `^\+(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$`, at least one group non-None, zero-total-duration raises `ValueError`. ISO 8601 fallback via `datetime.fromisoformat`. Cron strings pass through (caller decides).
  - **Depends on**: none

## Phase 2: Port Layer

- [x] 2.1 — Add `count_active_by_agent` to `ISchedulerRepository`
  - **Files**: `core/ports/outbound/scheduler_port.py`
  - **Requirements**: REQ-ST-7, REQ-ST-12
  - **Description**: Add abstract method `async def count_active_by_agent(self, agent_id: str) -> int` to `ISchedulerRepository`. SQL contract: `SELECT COUNT(*) FROM scheduled_tasks WHERE created_by = ? AND status NOT IN ('completed', 'failed', 'disabled')`.
  - **Depends on**: 1.1

## Phase 3: Use Case Layer

- [x] 3.1 — Guardrail in `ScheduleTaskUseCase.create_task`
  - **Files**: `core/use_cases/schedule_task.py`
  - **Requirements**: REQ-ST-7
  - **Description**: Before `repo.save_task`, call `await repo.count_active_by_agent(task.created_by)`. If count >= 21 raise `TooManyActiveTasksError`. Skip the guard when `created_by == ""` (CLI origin).
  - **Depends on**: 1.2, 2.1

## Phase 4: Adapter Layer

- [x] 4.1 — SQLite: `created_by` migration + `count_active_by_agent`
  - **Files**: `adapters/outbound/scheduler/sqlite_scheduler_repo.py`
  - **Requirements**: REQ-ST-12, REQ-ST-7
  - **Description**: In `ensure_schema`, add `ALTER TABLE scheduled_tasks ADD COLUMN created_by TEXT DEFAULT ''` wrapped in `try/except` (swallow "duplicate column"). Implement `count_active_by_agent`. Include `created_by` in all `save_task` / `get_task` / `list_tasks` row mappings.
  - **Depends on**: 2.1

- [x] 4.2 — Create `adapters/outbound/tools/scheduler_tool.py`
  - **Files**: `adapters/outbound/tools/scheduler_tool.py` (new)
  - **Requirements**: REQ-ST-1, REQ-ST-2, REQ-ST-3, REQ-ST-4, REQ-ST-5, REQ-ST-6, REQ-ST-8, REQ-ST-10
  - **Description**: Implement `SchedulerTool(ITool)` with `name = "scheduler"`. Constructor: `schedule_task_uc`, `agent_id`, `user_timezone`. Dispatch `_create`, `_list`, `_get`, `_update`, `_delete` based on `operation`. Apply validation rules: recurring+relative guard, zero-duration guard. List response shape: `{"tasks": [...], "total": N}`. Wrap all use case calls in try/except per REQ-ST-10.
  - **Depends on**: 1.3, 3.1

## Phase 5: Infrastructure & Wiring

- [x] 5.1 — Add `UserConfig(timezone)` to config
  - **Files**: `infrastructure/config.py`, `config/global.example.yaml`
  - **Requirements**: REQ-ST-11
  - **Description**: Add `UserConfig(timezone: str = "UTC")` Pydantic model and `user: UserConfig = UserConfig()` field on `GlobalConfig`. Update `global.example.yaml` with commented `user.timezone` example.
  - **Depends on**: none

- [x] 5.2 — `wire_scheduler()` on `AgentContainer` + `AppContainer` phase-3 call
  - **Files**: `infrastructure/container.py`
  - **Requirements**: REQ-ST-1, REQ-ST-9
  - **Description**: Add `wire_scheduler(schedule_task_uc, user_timezone) -> None` to `AgentContainer` with `_scheduler_wired: bool` idempotency guard. In `AppContainer`, add phase-3 loop calling `wire_scheduler()` on all agent containers after all are built — same ordering as `wire_delegation()`. No-op when `schedule_task_uc is None`.
  - **Depends on**: 4.2, 5.1

## Phase 6: Tests

- [x] 6.1 — Unit tests for `parse_schedule`
  - **Files**: `tests/unit/domain/test_time_parser.py` (new)
  - **Requirements**: REQ-ST-8 → SC-ST-1, SC-ST-7, SC-ST-9
  - **Description**: Parametrized pytest covering: relative units (`+5h`, `+2d`, `+30m`, `+1d2h30m`), zero-duration rejection (`+0m`, `+0d`), invalid formats (`+5x`, `+`, `+ 2h`), large offsets (`+999d`), ISO 8601 passthrough, cron passthrough.
  - **Depends on**: 1.3

- [x] 6.2 — Unit tests for `SchedulerTool`
  - **Files**: `tests/unit/adapters/test_scheduler_tool.py` (new)
  - **Requirements**: REQ-ST-2–6, REQ-ST-10 → SC-ST-1–8
  - **Description**: Mock `ISchedulerUseCase`. Test all 5 operations, recurring+relative guard, zero-duration guard, unknown operation, `BuiltinTaskProtectedError`, `TaskNotFoundError`, invalid trigger payload, internal exception fallback. Verify list response shape `{"tasks": [...], "total": N}`.
  - **Depends on**: 4.2

- [x] 6.3 — Unit tests for guardrail in use case
  - **Files**: `tests/unit/use_cases/test_schedule_task_guardrail.py` (new)
  - **Requirements**: REQ-ST-7 → SC-ST-4
  - **Description**: Mock `count_active_by_agent` returning 21; assert `TooManyActiveTasksError` raised. Mock returning 20; assert task saved. Verify CLI tasks (`created_by=""`) skip guard.
  - **Depends on**: 3.1

- [x] 6.4 — Unit tests for `wire_scheduler` idempotency
  - **Files**: `tests/unit/infrastructure/test_container_wire_scheduler.py` (new)
  - **Requirements**: REQ-ST-9 → SC-ST-10
  - **Description**: Call `wire_scheduler()` twice on same `AgentContainer`; assert tool executor contains exactly one `"scheduler"` entry. Call with `None` use case; assert no tool registered.
  - **Depends on**: 5.2

- [x] 6.5 — Integration test: SQLite migration + `count_active_by_agent`
  - **Files**: `tests/unit/adapters/test_sqlite_scheduler_created_by.py` (new)
  - **Requirements**: REQ-ST-12, REQ-ST-7
  - **Description**: Use in-memory SQLite DB. Verify `created_by` column added idempotently. Insert rows with different `created_by` values; assert `count_active_by_agent` counts only matching agent's non-terminal tasks.
  - **Depends on**: 4.1

---

## Traceability Matrix

| Requirement | Task(s) |
|-------------|---------|
| REQ-ST-1 | 4.2, 5.2 |
| REQ-ST-2 | 4.2, 6.2 |
| REQ-ST-3 | 4.2, 6.2 |
| REQ-ST-4 | 4.2, 6.2 |
| REQ-ST-5 | 4.2, 6.2 |
| REQ-ST-6 | 4.2, 6.2 |
| REQ-ST-7 | 1.2, 2.1, 3.1, 4.1, 6.3, 6.5 |
| REQ-ST-8 | 1.3, 4.2, 6.1 |
| REQ-ST-9 | 5.2, 6.4 |
| REQ-ST-10 | 4.2, 6.2 |
| REQ-ST-11 | 5.1 |
| REQ-ST-12 | 1.1, 4.1, 6.5 |
