# Proposal: Internal Cron-style Scheduler

## Intent
The current `ScheduleTaskUseCase` persists tasks to a JSON file, uses UUID string IDs, has no execution loop, and no dispatch logic — the code literally says "TODO: migrate to SQLite for production". We need a real, persistent, async-driven scheduler that can fire typed triggers (channel messages, LLM prompts, shell/CLI commands), survive restarts, and support both one-shot and recurrent tasks with retries and execution logging.

## Scope

### In Scope
- Replace JSON scheduler with SQLite-backed `ISchedulerRepository`
- 4 trigger types with typed payloads: `channel.send_message`, `agent.send_to_llm`, `shell_exec`, `cli_command`
- Integer ID system with builtin guard (id < 100 = system, protected from deletion; id >= 100 = user)
- `executions_remaining` countdown model (NULL = infinite, N = finite, 0 = completed)
- Async `SchedulerService` domain loop with heapq ordering, integrated into daemon startup
- `task_logs` table for per-execution structured logging
- Missed-task handling on restart (oneshot -> missed, recurrent -> recompute `next_run`)
- `SchedulerConfig` (Pydantic) with `max_retries`, `enabled`, `db_path`
- Builtin task seed: ID 1 `consolidate_memory` via `cli_command`

### Out of Scope
- Distributed/multi-node scheduling
- Per-task `max_retries` override (global only, via config)
- UI / inbound channel command surface (separate change)
- Migration of legacy JSON tasks (none in production yet)
- Recreation of manually deleted builtins

## Capabilities

### New Capabilities
- `scheduler-internal`: persistent cron-style task scheduler with typed triggers, builtin-guard, countdown execution, retries, and execution logging

### Modified Capabilities
- None (replaces the previous JSON prototype which had no spec)

## Approach
Hexagonal slice: domain entities (`ScheduledTask`, `TaskLog`) + inbound port (`ISchedulerUseCase`) + outbound port (`ISchedulerRepository` Protocol) + SQLite adapter (`aiosqlite` + `@asynccontextmanager`, mirroring `sqlite_history_store.py`) + use case (CRUD + builtin-guard) + domain `SchedulerService` (async loop, heapq by `next_run`, croniter for recurrent, retry with global `max_retries`). Wired in `AppContainer` and started during daemon lifecycle. Builtin tasks seeded at startup via `INSERT OR IGNORE`.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `core/domain/entities/task.py` | Modified | New int-ID entity with counter, typed trigger payload, status enum |
| `core/domain/entities/task_log.py` | New | Execution log entity |
| `core/ports/inbound/scheduler_port.py` | Modified | Updated ABC for int IDs, CRUD + enable/disable |
| `core/ports/outbound/scheduler_port.py` | New | `ISchedulerRepository` Protocol |
| `core/use_cases/schedule_task.py` | Modified | Replace JSON with repo-backed CRUD + builtin-guard |
| `core/domain/services/scheduler_service.py` | New | Async loop + dispatch for 4 trigger types |
| `adapters/outbound/scheduler/sqlite_scheduler_repo.py` | New | aiosqlite repo + schema bootstrap |
| `infrastructure/config.py` | Modified | Add `SchedulerConfig` |
| `infrastructure/container.py` | Modified | Wire scheduler into `AppContainer` lifecycle |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Clock drift / missed tasks on daemon restart | Med | Explicit missed-task handling; recurrent tasks recompute via croniter, skip past runs |
| Long-running `shell_exec`/`cli_command` blocking loop | Med | Per-task timeout enforced via `asyncio.wait_for`; dispatch in task group, not inline |
| Builtin ID collision if SQLite autoincrement < 100 | Low | Force user IDs via `MAX(id, 99) + 1`; reserve ids 1..99 for system |
| Concurrent CRUD vs loop iteration | Med | Single connection manager + heap invalidation on write; reload `next_run` after each mutation |
| `agent.send_to_llm` output with no channel flooding logs | Low | Store output in `task_logs.output` (bounded); truncate oversized payloads |

## Rollback Plan
1. Revert commit; re-enable the legacy `ScheduleTaskUseCase` JSON wiring in `AppContainer`
2. Stop `SchedulerService` in daemon lifecycle
3. Delete `data/scheduler.db` (no data migration path, feature is greenfield)
4. Legacy `schedules.json` is untouched by this change, so the old prototype is immediately restored

## Dependencies
- `aiosqlite` (already in project)
- `croniter` (new — required for recurrent `next_run` computation)
- `pydantic` (already in project)

## Success Criteria
- [ ] All 4 trigger types dispatch successfully in integration tests (in-memory SQLite)
- [ ] Builtin `consolidate_memory` task seeded at startup; cannot be deleted via use case
- [ ] Oneshot task with past `next_run` on restart -> status `missed`, logged, not executed
- [ ] Recurrent task with past `next_run` on restart -> `next_run` recomputed, no past runs executed
- [ ] Failing task retries up to `config.max_retries`, then transitions to `failed`
- [ ] Ruff + MyPy strict pass; pytest-asyncio suite green
- [ ] Scheduler loop idles (no spin) when no active tasks
