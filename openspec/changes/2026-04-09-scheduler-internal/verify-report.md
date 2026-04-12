# Verification Report

**Change**: scheduler-internal
**Version**: 1.0
**Mode**: Standard (no Strict TDD)

---

## Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 25 |
| Tasks complete | 25 |
| Tasks incomplete | 0 |

All 9 phases fully checked off in apply-progress.

---

## Build & Tests Execution

**Build**: ✅ Passed (mypy — only untyped-stub warning for `croniter`, no errors in project code)

**Tests**: ✅ 26 passed / ❌ 0 failed / ⚠️ 0 skipped

```
tests/unit/use_cases/test_schedule_task.py         8/8 passed
tests/unit/domain/test_scheduler_service.py        6/6 passed
tests/integration/scheduler/test_sqlite_scheduler_repo.py  7/7 passed
tests/integration/scheduler/test_scheduler_end_to_end.py   5/5 passed
Total: 26/26 passed in 0.21s
```

**Coverage**: Not measured (tool not configured for this run) → ➖ Not available

---

## Spec Compliance Matrix

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| FR-01: SQLite Schema | First startup — tables absent | `test_sqlite_scheduler_repo.py > test_ensure_schema_idempotent` | ✅ COMPLIANT |
| FR-01: SQLite Schema | Repeated startup — tables exist | `test_sqlite_scheduler_repo.py > test_ensure_schema_idempotent` | ✅ COMPLIANT |
| FR-02: Builtin Guard | Delete builtin task (id=1) | `test_schedule_task.py > test_delete_builtin_task_raises` | ✅ COMPLIANT |
| FR-02: Builtin Guard | Delete builtin task (id=99) | `test_schedule_task.py > test_delete_builtin_task_id_99_raises` | ✅ COMPLIANT |
| FR-02: Builtin Guard | User task deletion allowed | `test_schedule_task.py > test_delete_user_task_calls_repo` | ✅ COMPLIANT |
| FR-02: Builtin Guard | New user task ID allocation | `test_sqlite_scheduler_repo.py > test_first_user_task_gets_id_100`, `test_second_user_task_gets_id_101` | ✅ COMPLIANT |
| FR-03: Trigger Types | Valid channel.send_message payload | (no dedicated test) | ⚠️ PARTIAL |
| FR-03: Trigger Types | Unknown trigger type rejected | (no dedicated test — Pydantic discriminator handles it at model level, not use case) | ⚠️ PARTIAL |
| FR-04: Countdown | Oneshot completes after one execution | `test_scheduler_end_to_end.py > test_oneshot_completes_after_execution` | ✅ COMPLIANT |
| FR-04: Countdown | Infinite task remains active | `test_scheduler_end_to_end.py > test_recurrent_recomputes_next_run` | ✅ COMPLIANT |
| FR-04: Countdown | Countdown hits 0 → COMPLETED | `test_scheduler_end_to_end.py > test_recurrent_countdown_hits_zero_then_completed` | ✅ COMPLIANT |
| FR-05: Status Transitions | pending → running → pending (recurrent) | `test_scheduler_end_to_end.py > test_recurrent_recomputes_next_run` | ✅ COMPLIANT |
| FR-06: Async Loop | Loop idles with no active tasks | (no test for idle/sleep behavior — internal loop tested via unit mocks) | ⚠️ PARTIAL |
| FR-06: Async Loop | Loop wakes and dispatches due task | `test_scheduler_end_to_end.py > test_oneshot_completes_after_execution` | ✅ COMPLIANT |
| FR-07: Dispatch | agent.send_to_llm with no output_channel | `test_scheduler_end_to_end.py > test_agent_send_no_output_channel_stores_output` | ✅ COMPLIANT |
| FR-07: Dispatch | shell_exec with timeout exceeded | (no test) | ❌ UNTESTED |
| FR-08: Missed Task | Oneshot missed on restart | `test_scheduler_end_to_end.py > test_missed_oneshot_on_restart_marked_missed`, `test_scheduler_service.py > test_handle_missed_marks_oneshot_as_missed` | ✅ COMPLIANT |
| FR-08: Missed Task | Recurrent recomputed on restart | `test_scheduler_service.py > test_handle_missed_recomputes_recurrent_next_run` | ✅ COMPLIANT |
| FR-09: Retry Logic | Task retries and eventually fails | `test_scheduler_service.py > test_execute_task_retries_max_retries_then_failed` | ⚠️ PARTIAL |
| FR-09: Retry Logic | Task retries and succeeds on second attempt | (no test) | ❌ UNTESTED |
| FR-10: Task Logs | Successful execution logged | `test_scheduler_end_to_end.py > test_agent_send_no_output_channel_stores_output` | ✅ COMPLIANT |
| FR-10: Task Logs | Failed execution logged with error | (no dedicated test for failure log content) | ⚠️ PARTIAL |
| FR-11: Builtin Seed | Seeded on first startup | `test_sqlite_scheduler_repo.py > test_seed_builtin_idempotent` | ✅ COMPLIANT |
| FR-11: Builtin Seed | Seed idempotent | `test_sqlite_scheduler_repo.py > test_seed_builtin_idempotent` | ✅ COMPLIANT |
| FR-11: Builtin Seed | Builtin missing at dispatch time | (no test — error log + continue not covered) | ❌ UNTESTED |
| FR-12: SchedulerConfig | Config loaded from TOML | (no dedicated unit test — wiring tested implicitly via config.py structure) | ⚠️ PARTIAL |
| FR-12: SchedulerConfig | Config defaults when section absent | (no dedicated test) | ⚠️ PARTIAL |

**Compliance summary**: 17/26 scenarios fully compliant, 6 partial, 3 untested

---

## Correctness (Static — Structural Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| FR-01: SQLite schema — both tables with correct columns | ✅ Implemented | Both tables present; minor deviation: `created_at` stored as `TEXT`, not `REAL` as spec says. `next_run` is `REAL` as required. |
| FR-02: Builtin guard id < 100 in use case | ✅ Implemented | `delete_task` guards correctly |
| FR-02: Builtin guard in `update_task` | ❌ Missing | `update_task` allows modifying builtin tasks (id < 100) — no guard present |
| FR-03: 4 trigger types exist | ✅ Implemented | All 4 types defined |
| FR-03: Trigger type enum values match spec | ⚠️ Deviated | Spec says `"channel.send_message"` / `"agent.send_to_llm"` — implementation uses `"channel_send"` / `"agent_send"` (dots replaced by underscores). This affects any external API or serialized data contracts. |
| FR-03: ChannelSendPayload field `message` | ⚠️ Deviated | Spec says `message: str` — implementation uses `text: str` |
| FR-03: AgentSendPayload fields `prompt`, `output_channel` | ⚠️ Deviated | Spec says `prompt: str` — implementation uses `prompt_override: str \| None`. Field is optional and renamed. Adds `agent_id` and `tools_override` (extensions beyond spec). |
| FR-04: executions_remaining countdown semantics | ✅ Implemented | NULL=infinite, N=countdown, 0→completed all enforced |
| FR-04: executions_remaining MUST NOT go below 0 | ✅ Implemented | Guard `if remaining == 0: → COMPLETED` prevents going below 0 |
| FR-05: All 6 statuses exist and reachable | ✅ Implemented | pending, running, completed, failed, missed, disabled all defined |
| FR-06: Async loop with DB-ordered query | ✅ Implemented | Uses `get_next_due()` (ORDER BY next_run ASC) — no heapq as spec suggested, but DB ordering satisfies the requirement |
| FR-06: Loop sleeps up to 60s when no tasks | ✅ Implemented | `asyncio.wait_for(wake.wait(), timeout=60.0)` |
| FR-07: Dispatch for all 4 types | ✅ Implemented | All 4 types dispatched in `_dispatch_trigger` |
| FR-07: Timeout enforcement via asyncio.wait_for | ✅ Implemented | `_run_shell` and `_run_cli` use `asyncio.wait_for(proc.communicate(), timeout=...)` |
| FR-08: Missed task handling on restart | ✅ Implemented | `_handle_missed_on_startup` handles both oneshot→MISSED and recurrent→recompute |
| FR-09: retry_count incremented in DB per failure | ❌ Missing | `_execute_task` uses a local loop counter but NEVER writes `retry_count` to the DB. The repo's `update_after_execution` has no `retry_count` parameter. The DB column stays at 0 regardless of retries. |
| FR-09: retry_count resets to 0 on new cycle | ❌ Missing | Since retry_count is not tracked in DB, the reset invariant is also not enforced |
| FR-10: task_logs per-execution with atomic writes | ✅ Implemented | `save_log` per attempt; aiosqlite commit per operation |
| FR-10: output truncation at output_truncation_size | ✅ Implemented | `output[:self._config.output_truncation_size]` in `_finalize_task` |
| FR-11: Builtin seed ID=1 consolidate_memory | ✅ Implemented | `BUILTIN_CONSOLIDATE_MEMORY` seeded via `INSERT OR IGNORE` |
| FR-11: Missing builtin at dispatch → log error, don't recreate | ⚠️ Partial | No explicit guard in `_execute_task` for missing builtin; the loop would raise `TaskNotFoundError` naturally but no specific log message for this case |
| FR-12: SchedulerConfig with all required fields | ✅ Implemented | `enabled`, `db_path`, `max_retries`, `output_truncation_size` all present |
| FR-12: Loaded from `[scheduler]` TOML section | ✅ Implemented | `load_global_config` wires `SchedulerConfig(**merged.get("scheduler", {}))` |

---

## Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| `aiosqlite` with `@asynccontextmanager` | ✅ Yes | Pattern exactly as designed |
| `INSERT OR IGNORE` for builtin seed | ✅ Yes | Correctly implemented |
| `ISchedulerRepository` as Protocol | ✅ Yes | In `core/ports/outbound/scheduler_port.py` |
| `ILLMDispatcher` as Protocol | ✅ Yes | In `core/ports/outbound/llm_dispatcher_port.py` |
| `_run_once()` test helper | ✅ Yes | Present and used in integration tests |
| User task IDs via `COALESCE(MAX(id), 99) + 1 WHERE id >= 100` | ✅ Yes | Exactly implemented |
| Integration tests use `tmp_path` not `:memory:` | ✅ Yes | All integration fixtures use `tmp_path` |
| Dispatch adapters in `adapters/outbound/scheduler/dispatch_adapters.py` | ✅ Yes | `ChannelSenderAdapter`, `LLMDispatcherAdapter`, `SchedulerDispatchPorts` all present |
| `SchedulerService` depends only on ports (domain layer) | ⚠️ Deviated | Runtime import of `BUILTIN_CONSOLIDATE_MEMORY` from `adapters.outbound.scheduler.builtin_tasks` inside `start()` — see import rule violation below |

---

## Import Rule Analysis

| File | Violation? | Detail |
|------|-----------|--------|
| `core/domain/services/scheduler_service.py` line 35 | ✅ Safe | `from adapters...SchedulerDispatchPorts` is under `TYPE_CHECKING` — not imported at runtime |
| `core/domain/services/scheduler_service.py` line 61 | ❌ VIOLATION | `from adapters.outbound.scheduler.builtin_tasks import BUILTIN_CONSOLIDATE_MEMORY` inside `start()` — **runtime import from `adapters/` in a domain service**. Domain layer MUST NOT import from `adapters/`. |

---

## Issues Found

### CRITICAL (must fix before archive)

1. **FR-09: `retry_count` never written to DB** — `SchedulerService._execute_task` tracks attempts via a local loop variable but never reads or writes `retry_count` to the database. The spec invariant "retry_count increments on each failure, resets to 0 on new cycle" is completely unimplemented at the persistence layer. The `update_after_execution` repo method has no `retry_count` parameter. The DB column always stays at `0`. Fix: pass `retry_count` to `update_after_execution` signature and SET it in the SQL UPDATE; reset it in `_finalize_task` via the same method.

2. **Import rule violation: domain imports from adapter at runtime** — `core/domain/services/scheduler_service.py` line 61 does a runtime import `from adapters.outbound.scheduler.builtin_tasks import BUILTIN_CONSOLIDATE_MEMORY` inside `start()`. The domain layer MUST NOT import from `adapters/`. Fix: inject `builtin_tasks: list[ScheduledTask]` into `SchedulerService.__init__()` and pass `[BUILTIN_CONSOLIDATE_MEMORY]` from the container wiring in `infrastructure/container.py`.

3. **FR-02: `update_task` does not protect builtin tasks** — `ScheduleTaskUseCase.update_task` calls `save_task(updated)` without checking `task_id < 100`. A caller can freely overwrite any builtin task's properties. The spec invariant "Tasks with id < 100 MUST NOT be modifiable" is violated. Fix: add `if task_id < 100: raise BuiltinTaskProtectedError(...)` at the top of `update_task`.

### WARNING (should fix)

4. **FR-03: Trigger type enum values differ from spec strings** — Spec defines `"channel.send_message"` and `"agent.send_to_llm"` as the trigger type identifiers. Implementation uses `"channel_send"` and `"agent_send"`. Any external serialized data (DB rows, API payloads, documentation) will use the snake_case values, not the spec's dot-notation values. This is a coherent internal choice but breaks the spec contract.

5. **FR-03: `ChannelSendPayload.text` vs spec's `message`** — Spec defines `message: str` as the payload field. Implementation uses `text: str`. This is a field name deviation that affects DB serialization and any consumers of the JSON payload.

6. **FR-03: `AgentSendPayload` deviates from spec** — Spec defines `prompt: str` (required). Implementation has `prompt_override: str | None` (optional, renamed). An `agent_send` task can be created without a prompt. Also adds `agent_id` (required, not in spec) and `tools_override` (not in spec). The spec's field contract is not preserved.

7. **FR-01: `created_at` stored as `TEXT`, spec says `REAL`** — The spec schema requires `created_at REAL` (Unix timestamp). Implementation stores it as `TEXT NOT NULL` (ISO 8601 string). Minor inconsistency; functionally equivalent but schema doesn't match spec.

8. **FR-09: No test covering retry_count DB persistence** — The existing retry test (`test_execute_task_retries_max_retries_then_failed`) only verifies attempt count and final status. It does not verify the `retry_count` column in the DB is updated. Once the critical fix is applied, a corresponding test is needed.

9. **FR-11: No test for "builtin missing at dispatch time → log error, continue"** — The FR-11 scenario 3 is untested. No test verifies that a manually deleted builtin task ID causes an error log and allows the loop to continue without crashing.

10. **FR-07: No test for shell_exec timeout exceeded** — The timeout scenario (FR-07 scenario 2) is documented in the spec but has no test coverage.

### SUGGESTION (nice to have)

11. **`SchedulerConfig.output_truncation_size` not in spec FR-12** — The field is a reasonable extension and well-placed, but FR-12 only lists `max_retries`, `enabled`, `db_path`. The field is present in `global.yaml` and adds value; just noting it as an undocumented extension.

12. **`test_agent_send_no_output_channel_stores_output` directly queries `aiosqlite`** — The test accesses `repo._db_path` and opens a raw `aiosqlite` connection to verify log content. It should use `repo.save_log` / a `get_logs()` port method if available, or at minimum `repo._db_path` access should be documented as intentional for test purposes.

13. **`asyncio_default_fixture_loop_scope` deprecation warning** — `pyproject.toml` should set `asyncio_default_fixture_loop_scope = "function"` in `[tool.pytest.ini_options]` to silence the pytest-asyncio deprecation warning.

---

## Verdict

**FAIL**

3 critical issues prevent archive:
- `retry_count` is never persisted to the DB (FR-09 invariant broken)
- Domain service imports from `adapters/` at runtime (hexagonal architecture rule violated)
- `update_task` does not protect builtin tasks from modification (FR-02 invariant incomplete)

All 26 tests pass. Fix the 3 criticals, add missing tests for the fixed behavior, then re-verify.
