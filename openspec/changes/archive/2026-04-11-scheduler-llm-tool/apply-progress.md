# Apply Progress: scheduler-llm-tool

## Status: complete (all 14 tasks done)

## Completed Tasks

- [x] 1.1 — Add `created_by: str = ""` to `ScheduledTask`
- [x] 1.2 — Add `TooManyActiveTasksError` to `errors.py`
- [x] 1.3 — Create `core/domain/utils/time_parser.py`
- [x] 2.1 — Add `count_active_by_agent` to `ISchedulerRepository`
- [x] 3.1 — Guardrail in `ScheduleTaskUseCase.create_task`
- [x] 4.1 — SQLite: `created_by` migration + `count_active_by_agent`
- [x] 5.1 — Add `UserConfig(timezone)` to config
- [x] 6.1 — Unit tests for `parse_schedule`
- [x] 4.2 — Create `adapters/outbound/tools/scheduler_tool.py`
- [x] 5.2 — `wire_scheduler()` on `AgentContainer` + `AppContainer` phase-3 call
- [x] 6.3 — Unit tests for guardrail in use case
- [x] 6.5 — Integration test: SQLite migration + `count_active_by_agent`
- [x] 6.2 — Unit tests for `SchedulerTool`
- [x] 6.4 — Unit tests for `wire_scheduler` idempotency

## Pending Tasks
(none — all 14 tasks complete)

## Files Changed

- `core/domain/entities/task.py` — added `created_by: str = ""`
- `core/domain/errors.py` — added `TooManyActiveTasksError(SchedulerError)`
- `core/domain/utils/__init__.py` — new (empty package init)
- `core/domain/utils/time_parser.py` — new; `parse_schedule(raw, user_timezone) -> datetime`
- `core/ports/outbound/scheduler_port.py` — added `count_active_by_agent` abstract method
- `core/use_cases/schedule_task.py` — guardrail in `create_task`: count active, raise `TooManyActiveTasksError` if >= 21, skip when `created_by == ""`
- `adapters/outbound/scheduler/sqlite_scheduler_repo.py` — `created_by` migration in `_ensure_schema_conn`; `count_active_by_agent` method; `created_by` in all row mappings (save_task insert, upsert, seed_builtin, _row_to_task); `ensure_schema` now delegates to `_ensure_schema_conn`
- `infrastructure/config.py` — added `UserConfig(timezone: str = "UTC")`, wired into `GlobalConfig.user` and `load_global_config`
- `config/global.example.yaml` — added `[user]` section with commented `timezone` examples
- `tests/unit/domain/test_time_parser.py` — new; parametrized tests for `parse_schedule`
- `adapters/outbound/tools/scheduler_tool.py` — new; `SchedulerTool(ITool)` multi-op dispatcher
- `infrastructure/container.py` — `AgentContainer.wire_scheduler()` + `_scheduler_wired` guard + `AppContainer` phase-3 loop after scheduler wiring block
- `tests/unit/use_cases/test_schedule_task_guardrail.py` — new; guardrail tests (count>=21 raises, count=20 saves, CLI tasks skip guard)
- `tests/unit/adapters/test_sqlite_scheduler_created_by.py` — new; integration tests (idempotent migration, count isolation per agent, terminal status exclusion, CLI rows excluded)
- `tests/unit/adapters/tools/test_scheduler_tool.py` — new; unit tests for SchedulerTool (all 5 ops, validation, error handling, created_by injection, LLM kind mapping)
- `tests/unit/infrastructure/test_container_wire_scheduler.py` — new; unit tests for wire_scheduler idempotency (6 tests)

## Implementation Notes

### 1.1 — `created_by` placement
Added after `status` field (before `retry_count`) to keep ordering logical: task identity fields first, then runtime state fields. Default `""` aligns with design decision (legacy rows don't count against guardrail cap).

### 1.2 — `TooManyActiveTasksError`
Added `agent_id` as constructor param to produce the exact message template from the spec: `"Agent {agent_id} has reached the maximum of 21 active tasks"`. Also stored as `self.agent_id` for callers that need to inspect it.

### 1.3 — `parse_schedule`
- `_RELATIVE_RE` regex: `^\+(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$`
- Edge case handled: bare `+` matches the regex (all groups are None) — explicit guard raises `ValueError` with clear message.
- Zero-duration check: sum all groups as total_minutes; zero raises `ValueError`.
- ISO 8601 fallback uses `datetime.fromisoformat` — Python 3.11+ supports full ISO 8601 including `Z` suffix.
- `user_timezone` param is accepted but reserved for future use (noted in docstring). Relative schedules always return UTC-aware datetimes; ISO 8601 returns whatever tzinfo the string carries.

### 2.1 — `count_active_by_agent`
Added as the last method of `ISchedulerRepository` Protocol. Signature: `async def count_active_by_agent(self, agent_id: str) -> int`. SQL contract from spec: `SELECT COUNT(*) FROM scheduled_tasks WHERE created_by = ? AND status NOT IN ('completed', 'failed', 'disabled')`.

### 3.1 — Guardrail in `create_task`
Guard is skipped when `task.created_by == ""` (CLI origin — no agent cap enforced). When a non-empty `created_by` is present, `count_active_by_agent` is called before `repo.save_task`. Count >= 21 raises `TooManyActiveTasksError(agent_id=task.created_by)`. Import added to `schedule_task.py`.

### 4.1 — SQLite `created_by` migration
- `_ensure_schema_conn` now runs `ALTER TABLE scheduled_tasks ADD COLUMN created_by TEXT DEFAULT ''` wrapped in broad `except Exception` checking `"duplicate column"` in the error string — matches the aiosqlite/sqlite3 error message pattern. Other exceptions are re-raised.
- `ensure_schema` (public) now delegates entirely to `_ensure_schema_conn` — removes duplication.
- `save_task` insert (id==0): added `created_by` to column list and value tuple (16 params now).
- `save_task` upsert (explicit id): added `created_by` to column list, value tuple, and `ON CONFLICT DO UPDATE SET` clause.
- `seed_builtin`: added `created_by` to column list and value tuple.
- `_row_to_task`: reads `row["created_by"]` with `None` fallback to `""` (handles rows created before migration).
- `count_active_by_agent`: new method using `SELECT COUNT(*) AS cnt ... WHERE created_by = ? AND status NOT IN ('completed', 'failed', 'disabled')`.

### 5.1 — `UserConfig`
- Placed before `DelegationConfig` in `config.py` (alphabetically/logically consistent with other sub-configs).
- Added to `_render_default_global_yaml` so new `global.yaml` files generated on first boot include the `user` section.
- `global.example.yaml`: added full section with IANA timezone examples (Argentina, España, New York).

### 4.2 — `SchedulerTool`
- Follows `WebSearchTool` multi-op pattern: `operation` enum dispatches to private `_create/_list/_get/_update/_delete` methods.
- Constructor: `schedule_task_uc: ISchedulerUseCase`, `agent_id: str`, `user_timezone: str` (keyword-only, matching design contract).
- TaskKind translation: domain uses `"oneshot"`/`"recurrent"` values; LLM-facing schema uses `"one_shot"`/`"recurring"`. Mappings `_TASK_KIND_TO_LLM` / `_LLM_TO_TASK_KIND` handle round-trip translation.
- `trigger_payload` validation: injects `"type"` discriminator key before calling `model_validate` on the concrete Pydantic model.
- `_update`: trigger_payload update requires a `get_task` call first to resolve the trigger_type — the design did not address this but it's the only safe way to validate the payload model class.
- Schedule parsing for `_update`: same dual-format logic as `_create` (relative → parse_schedule, ISO 8601 → validate then keep raw string).
- All use case exceptions caught per REQ-ST-10. No exception escapes `execute()`.
- `consolidate_memory` trigger type intentionally excluded from `_ALLOWED_TRIGGER_TYPES` (system-only).

### 5.2 — `wire_scheduler`
- `_scheduler_wired: bool = False` added to `AgentContainer.__init__` alongside `_delegation_wired`.
- `wire_scheduler(schedule_task_uc, user_timezone)` mirrors `wire_delegation`: lazy import of `SchedulerTool` inside the method, single idempotency check, try/except per-agent in `AppContainer` loop.
- Phase-3 loop placed AFTER the full scheduler wiring block (`schedule_task_uc`, `scheduler_service`) in `AppContainer.__init__` — this is important: `schedule_task_uc` doesn't exist until that block runs.
- No `if scheduler.enabled` guard in `wire_scheduler`: the method already guards via `if schedule_task_uc is None` (AppContainer always builds it, so it will always wire; disabling would require a config flag check if added later).
- Type hint uses `ScheduleTaskUseCase | None` (no quotes needed — `from __future__ import annotations` is already at module top).

### 6.3 — `test_schedule_task_guardrail.py`
- Follows the mock pattern from `test_schedule_task.py`: `AsyncMock` repo + `MagicMock` on_mutation.
- `save_task.side_effect = lambda task: task` used in fixtures to avoid needing explicit `return_value` per test.
- Tests count=21 and count=22 both raise; count=20 and count=0 save normally.
- CLI tests: `count_active_by_agent.assert_not_awaited()` verifies the guard is skipped (not just that it passed).
- Extra CLI test covers the paranoia case: even if count mock returns 999, no raise occurs.

### 6.5 — `test_sqlite_scheduler_created_by.py`
- Uses `tmp_path` fixture (pytest built-in) instead of `:memory:` because `SQLiteSchedulerRepo` takes a file path string; `tmp_path / "test_scheduler.db"` gives full isolation.
- Idempotency test calls `ensure_schema()` twice then verifies column via raw aiosqlite insert+select.
- Multi-agent isolation test inserts rows for agent-a (2) and agent-b (1), checks both counts independently.
- Terminal status test uses `update_status()` to force completed/failed/disabled — matches production code path.
- CLI rows test verifies that `count_active_by_agent("")` returns 3 (correct count for the CLI bucket) and `count_active_by_agent("agent-a")` returns 1.

### 6.2 — `test_scheduler_tool.py`
- File placed under `tests/unit/adapters/tools/` (alongside `test_delegate_tool.py`) — matches the existing structure.
- Uses `_make_tool()` factory that returns `(SchedulerTool, mock_uc)` tuple where mock_uc has all methods as `AsyncMock`. Mirrors `_make_tool()` pattern from `test_delegate_tool.py`.
- `_make_task()` helper produces a valid `ScheduledTask` with `ChannelSendPayload` — avoids repeated boilerplate across tests.
- `created_by` injection test: passes `created_by="malicious-agent"` in kwargs and asserts `call_arg.created_by == "injected-agent"` from constructor.
- LLM kind mapping tested in both directions: `create` (one_shot→ONESHOT, recurring→RECURRENT) and `list` (ONESHOT→one_shot, RECURRENT→recurring).
- `update` with `+1h` schedule: asserts the schedule argument passed to `update_task` is NOT the raw "+1h" and contains "T" (ISO 8601 marker).
- All error paths verified to return `ToolResult(success=False)` — never raise.
- `TooManyActiveTasksError` test: verifies agent_id or "21" appears in error message.
- `_error` helper test: verifies `tool_name == "scheduler"` and `error` field is not None.

### 6.4 — `test_container_wire_scheduler.py`
- Uses identical `__new__` + manual attribute injection pattern from `test_container.py`.
- `_make_mock_use_case()` uses `MagicMock(spec=ScheduleTaskUseCase)` — passes spec-based isinstance checks.
- `_build_minimal_container()` includes `_scheduler_wired = False` initialization (mirrors `_delegation_wired = False`).
- 6 tests: idempotency (double call), None no-op, happy path (agent_id + timezone + uc reference), flag set/not-set, None then real (flag recovery), idempotency with different args (first call wins).
- Asserts `tool._uc is uc` to verify the exact use case instance was passed through (identity, not just equality).

### 6.1 — `test_time_parser.py`
- Used `freezegun` (`@freeze_time`) to freeze `datetime.now` — consistent with the project pattern used in `test_scheduler_service.py`.
- Parametrized relative valid cases: `+5h`, `+2d`, `+30m`, `+1d2h30m`, `+999d`, `+1d0h1m`.
- Zero-duration parametrized: `+0m`, `+0d`, `+0h`, `+0d0h0m`, `+0d0h`, `+0h0m`.
- Invalid formats parametrized: 8 cases covering wrong unit, space, double-plus, wrong order, uppercase, missing plus, empty string, garbage prefix.
- Bare `+` tested separately to assert the specific "at least one" error message.
- ISO 8601 passthrough: 4 cases (Z suffix, UTC offset, negative offset, naive).
- `user_timezone` param acceptance test (future-proof, no effect on output today).
