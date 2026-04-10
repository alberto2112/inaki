# Archive Report: scheduler-internal

**Change**: scheduler-internal  
**Archived**: 2026-04-09  
**Status**: ✅ ARCHIVED  
**Artifact Store**: Hybrid (Engram + Filesystem)

---

## Change Summary

Implemented a persistent, async-driven cron-style task scheduler for Iñaki. The scheduler runs as an internal daemon service with SQLite backing, supports four typed trigger types (channel messages, LLM prompts, shell commands, CLI commands), enforces integer IDs with builtin protection (id < 100), manages task countdown execution models, retries failed tasks with configurable limits, handles missed tasks on daemon restart, and maintains structured per-execution logging via a `task_logs` table. The scheduler integrates into the daemon lifecycle via `AppContainer` startup/shutdown and uses hexagonal architecture with domain entities, ports, adapters, and use cases.

---

## Artifacts Lineage

All artifacts persisted to Engram with full observation IDs for traceability:

| Artifact | ID | Topic Key |
|----------|----|-----------| 
| Proposal | 610 | sdd/scheduler-internal/proposal |
| Spec | 611 | sdd/scheduler-internal/spec |
| Design | 612 | sdd/scheduler-internal/design |
| Tasks | 613 | sdd/scheduler-internal/tasks |
| Apply Progress | 615 | sdd/scheduler-internal/apply-progress |
| Verify Report | 618 | sdd/scheduler-internal/verify-report |

---

## Post-Verify Fixes Applied

All 3 critical issues identified in verification were fixed BEFORE archiving:

### 1. **FR-09: `retry_count` Persistence** ✅ FIXED
- **Issue**: `_execute_task` tracked retry attempts via a local loop counter but never persisted `retry_count` to the database.
- **Fix**: Modified `update_after_execution(task_id, ..., retry_count)` signature to accept `retry_count` parameter and persist it in the UPDATE statement. Retry count now incremented on each failure and reset to 0 in `_finalize_task` after successful execution.
- **File**: `core/domain/services/scheduler_service.py` (lines 150, 167, 201)

### 2. **Domain Import Rule Violation** ✅ FIXED
- **Issue**: `SchedulerService.start()` performed a runtime import `from adapters.outbound.scheduler.builtin_tasks import BUILTIN_CONSOLIDATE_MEMORY`, violating hexagonal architecture (domain must never import from adapters at runtime).
- **Fix**: Injected `builtin_tasks: list[ScheduledTask] | None` into `SchedulerService.__init__()` and pass `[BUILTIN_CONSOLIDATE_MEMORY]` from `AppContainer` during wiring. Domain service now receives builtin tasks as a constructor parameter.
- **File**: `core/domain/services/scheduler_service.py` (lines 49, 54, 64)

### 3. **FR-02: `update_task` Builtin Guard** ✅ FIXED
- **Issue**: `ScheduleTaskUseCase.update_task()` did not check `task_id < 100` before allowing modifications. Any caller could overwrite builtin task properties.
- **Fix**: Added guard at the top of `update_task`: `if task_id < 100: raise BuiltinTaskProtectedError(...)`. Now builtin tasks (id < 100) are immutable via the use case, matching the delete protection.
- **File**: `core/use_cases/schedule_task.py`

---

## Test Results

**All 26 tests pass after fixes**:

```
tests/unit/use_cases/test_schedule_task.py         8/8 passed ✅
tests/unit/domain/test_scheduler_service.py        6/6 passed ✅
tests/integration/scheduler/test_sqlite_scheduler_repo.py  7/7 passed ✅
tests/integration/scheduler/test_scheduler_end_to_end.py   5/5 passed ✅
---
Total: 26/26 passed in 0.15s ✅
```

Build: ✅ mypy clean (only untyped-stub warning for croniter, no project errors)

---

## Files Created and Modified

### New Files

**Domain Layer:**
- `core/domain/entities/task_log.py` — TaskLog Pydantic model for execution log entries
- `core/domain/services/scheduler_service.py` — Main async scheduler loop with dispatch, missed-task handling, and retry logic
- `core/domain/errors.py` — Added SchedulerError, BuiltinTaskProtectedError, InvalidTriggerTypeError, TaskNotFoundError

**Port Interfaces:**
- `core/ports/outbound/scheduler_port.py` — ISchedulerRepository Protocol (CRUD + schema management)
- `core/ports/outbound/llm_dispatcher_port.py` — ILLMDispatcher Protocol (narrow port for LLM dispatch)

**Adapter Layer:**
- `adapters/outbound/scheduler/__init__.py` — Package marker
- `adapters/outbound/scheduler/sqlite_scheduler_repo.py` — SQLiteSchedulerRepo using aiosqlite with async context manager
- `adapters/outbound/scheduler/builtin_tasks.py` — BUILTIN_CONSOLIDATE_MEMORY constant definition
- `adapters/outbound/scheduler/dispatch_adapters.py` — ChannelSenderAdapter, LLMDispatcherAdapter, SchedulerDispatchPorts

**Tests:**
- `tests/unit/use_cases/test_schedule_task.py` — 8 unit tests for CRUD and builtin guard
- `tests/unit/domain/test_scheduler_service.py` — 6 unit tests for service logic (missed tasks, retry, finalize)
- `tests/integration/scheduler/__init__.py` — Package marker
- `tests/integration/scheduler/test_sqlite_scheduler_repo.py` — 7 integration tests for SQLite adapter
- `tests/integration/scheduler/test_scheduler_end_to_end.py` — 5 integration tests for full scheduler flow

### Modified Files

**Domain Layer:**
- `core/domain/entities/task.py` — Rewrote task entity: UUID → int ID, added TaskKind and TriggerType enums, discriminated TriggerPayload union, updated ScheduledTask model

**Use Cases:**
- `core/use_cases/schedule_task.py` — Full rewrite: JSON → SQLite-backed repository, added builtin guard (delete + update), mutation callback for loop invalidation

**Ports:**
- `core/ports/inbound/scheduler_port.py` — Updated ISchedulerUseCase with int IDs and full CRUD interface

**Infrastructure:**
- `infrastructure/config.py` — Added SchedulerConfig(BaseModel) with fields: enabled, db_path, max_retries, output_truncation_size; integrated into GlobalConfig.scheduler
- `infrastructure/container.py` — Wired scheduler_repo, schedule_task_uc, scheduler_service, dispatch_ports; added startup()/shutdown() lifecycle methods
- `config/global.yaml` — Added [scheduler] section with defaults

**Interface:**
- `adapters/inbound/daemon/runner.py` — Integrated app_container.startup() at daemon start and app_container.shutdown() at end

**Dependencies:**
- `pyproject.toml` — Added croniter>=2.0 to dependencies, freezegun>=1.5 to dev dependencies

---

## Spec Synced to Main

New main spec created (first time for this domain):

```
openspec/specs/scheduler-internal/spec.md
```

This is the source of truth for the scheduler-internal domain, covering all 12 functional requirements (FR-01 through FR-12) and 4 non-functional requirements (NFR-01 through NFR-04).

---

## Known Deviations from Spec (Documented)

These are intentional design choices documented and acceptable:

1. **Trigger Type Enum Values** — Spec uses dot-notation (`"channel.send_message"`, `"agent.send_to_llm"`); implementation uses snake_case (`"channel_send"`, `"agent_send"`). This is internal and consistent.

2. **ChannelSendPayload Field** — Spec defines `message: str`; implementation uses `text: str`. Functionally equivalent.

3. **AgentSendPayload Deviation** — Spec defines `prompt: str` (required); implementation uses `prompt_override: str | None` (optional) plus adds `agent_id` and `tools_override`. These are intentional extensions for flexibility.

4. **created_at Schema** — Spec requires `REAL`; implementation stores as `TEXT NOT NULL` (ISO 8601 string). Functionally equivalent and easier to read in raw SQL queries.

5. **output_truncation_size** — Not in spec FR-12 but added to `SchedulerConfig` for preventing unbounded storage growth. Defaults to 64 KB.

---

## SDD Cycle Summary

| Phase | Status | Artifacts | Duration |
|-------|--------|-----------|----------|
| Proposal | ✅ Complete | proposal.md (25 tasks, 9 phases) | — |
| Spec | ✅ Complete | spec.md (12 FR, 4 NFR, 10 scenarios) | — |
| Design | ✅ Complete | design.md (11 sections, code sketches) | — |
| Tasks | ✅ Complete | tasks.md (25 tasks across 9 phases) | — |
| Apply | ✅ Complete | 25/25 tasks implemented + 3 post-verify fixes | — |
| Verify | ✅ Complete (with fixes) | verify-report.md (26/26 tests pass, 3 criticals fixed) | — |
| Archive | ✅ Complete | archive-report.md (this document) | — |

---

## Validation Checklist

- [x] All 26 tests pass (unit + integration)
- [x] All 3 critical issues fixed and verified
- [x] Hexagonal architecture maintained (domain, ports, adapters, use cases)
- [x] Builtin task protection enforced (delete + update)
- [x] Async loop non-spinning (60-second max idle)
- [x] SQLite persistence with atomic writes (one transaction per operation)
- [x] Missed-task handling on restart (oneshot → missed, recurrent → recompute)
- [x] Retry logic with configurable max_retries (default 3)
- [x] Four trigger types dispatched (channel, LLM, shell, CLI)
- [x] Timeout enforcement via asyncio.wait_for
- [x] Task execution logging to task_logs table
- [x] Builtin task seed via INSERT OR IGNORE
- [x] mypy clean build
- [x] Spec synced to main specs directory
- [x] Change folder moved to archive with date prefix

---

## Ready for Next Change

The scheduler-internal change is fully archived and the source of truth (spec, design, tasks) is persisted in Engram and filesystem. All code is production-ready with 26 passing tests and zero blocking issues.

**Next steps**: Start a new change or iterate on feature requests for the scheduler (e.g., UI for task management, additional trigger types, distributed scheduling).
