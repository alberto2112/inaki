# Design: scheduler-llm-tool

## Technical Approach

Expose the existing `ScheduleTaskUseCase` (full CRUD) to the LLM as a single multi-operation `SchedulerTool` implementing `ITool`. Follows the proven `WebSearchTool` multi-op pattern (operation enum dispatching to private methods). Two-phase wiring mirrors `wire_delegation()`. Relative time parsing (`+Xd Yh Zm`) is a pure function in domain utils -- no external dependencies.

## Architecture Decisions

| Decision | Choice | Alternative | Rationale |
|----------|--------|-------------|-----------|
| Tool multiplexing | Single tool with `operation` enum | 5 separate tools | Matches `WebSearchTool` pattern; one registry entry; LLM selects operation via parameter |
| Time parsing location | `core/domain/utils/time_parser.py` | Private method on SchedulerTool | Pure domain logic (time arithmetic); reusable by CLI or other adapters; independently testable |
| Relative time regex | `^\+(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$` | dateparser lib | Zero dependencies; covers `+5h`, `+2d23h5m`, `+30m`; rejects zero-total-duration (`+0m`) |
| Recurring + relative guard | Reject `+` prefix on `task_kind="recurring"` | Silent pass-through | Invalid by definition — recurring needs cron, not offset; fail fast with clear message |
| Guardrail location | `ScheduleTaskUseCase.create_task` | Inside SchedulerTool | Domain invariant belongs in use case; CLI and tool both respect it |
| `created_by` default | Empty string `""` | `None` / `"cli"` | SQLite `ALTER TABLE ADD COLUMN ... DEFAULT ''` is safe; legacy rows don't count against any agent's cap |
| Config model | `UserConfig(timezone)` on `GlobalConfig` | Flat field on GlobalConfig | Follows existing sub-config pattern (`AppConfig`, `SchedulerConfig`, etc.) |
| Wiring pattern | `wire_scheduler()` on AgentContainer | Constructor injection | `ScheduleTaskUseCase` is AppContainer singleton, unavailable at AgentContainer.__init__ time; mirrors `wire_delegation()` |

## Data Flow

```
LLM tool_call("scheduler", operation="create", ...)
       |
       v
SchedulerTool.execute(**kwargs)
       |
       +---> _parse_schedule(schedule_str)     # relative → abs datetime
       +---> _build_task(...)                   # ScheduledTask entity
       |
       v
ScheduleTaskUseCase.create_task(task)
       |
       +---> count_active_by_agent(agent_id)    # guardrail check
       +---> repo.save_task(task)               # persist
       +---> on_mutation()                      # invalidate scheduler cache
       |
       v
ToolResult(success=True, output=JSON summary)
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `adapters/outbound/tools/scheduler_tool.py` | Create | `SchedulerTool(ITool)` with operation dispatch, constructor-injected `schedule_task_uc`, `agent_id`, `user_timezone` |
| `core/domain/utils/time_parser.py` | Create | `parse_schedule(raw: str, user_tz: str) -> datetime` -- relative `+Xd Yh Zm` or ISO 8601 fallback |
| `core/domain/entities/task.py` | Modify | Add `created_by: str = ""` field to `ScheduledTask` |
| `core/domain/errors.py` | Modify | Add `TooManyActiveTasksError(SchedulerError)` |
| `core/ports/outbound/scheduler_port.py` | Modify | Add `count_active_by_agent(agent_id: str) -> int` to `ISchedulerRepository` |
| `core/use_cases/schedule_task.py` | Modify | Guardrail in `create_task`: count active, raise `TooManyActiveTasksError` if >= 21 |
| `adapters/outbound/scheduler/sqlite_scheduler_repo.py` | Modify | `ALTER TABLE ADD COLUMN created_by`; implement `count_active_by_agent`; include `created_by` in save/read |
| `infrastructure/config.py` | Modify | Add `UserConfig(timezone: str = "UTC")` + `GlobalConfig.user: UserConfig` |
| `infrastructure/container.py` | Modify | `AgentContainer.wire_scheduler()` + AppContainer phase-3 call after scheduler wiring |
| `config/global.example.yaml` | Modify | Add `user.timezone` example |

## Interfaces / Contracts

### SchedulerTool constructor

```python
class SchedulerTool(ITool):
    name = "scheduler"
    
    def __init__(
        self,
        *,
        schedule_task_uc: ISchedulerUseCase,
        agent_id: str,
        user_timezone: str,
    ) -> None: ...
```

### parse_schedule

```python
def parse_schedule(raw: str, user_timezone: str) -> datetime:
    """
    '+2d5h30m' → now + timedelta(days=2, hours=5, minutes=30) (UTC)
    '2026-04-12T14:00:00-03:00' → parsed as-is
    Raises ValueError on invalid input or zero-duration relative.
    """
```

Regex: `^\+(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$` -- at least one group must be non-None.

### ISchedulerRepository addition

```python
async def count_active_by_agent(self, agent_id: str) -> int: ...
```

SQL: `SELECT COUNT(*) FROM scheduled_tasks WHERE created_by = ? AND status NOT IN ('completed', 'failed', 'disabled')`

### JSON schema (LLM-facing)

The `parameters_schema` uses a flat object with `operation` enum. Trigger payloads are nested under `trigger_payload` with `type` discriminator. Key properties:

- `operation`: enum `[create, list, get, update, delete]`
- `task_id`: integer (required for get/update/delete)
- `name`, `task_kind`, `trigger_type`, `trigger_payload`, `schedule`: required for create
- `schedule` description explains dual format: `"+5h" or "+2d23h5m" for relative, ISO 8601 for absolute, cron for recurrent`
- `trigger_type`: enum `[channel_send, agent_send, shell_exec]` (consolidate_memory excluded)

### Validation rules (tool layer)

1. If `task_kind == "recurring"` and `schedule[0] == '+'` → `ToolResult(success=False, error="Recurring tasks require a cron expression, not a relative time offset")`
2. If relative schedule resolves to zero duration (`+0m`, `+0d`, `+0d0h0m`) → `ToolResult(success=False, error="Relative schedule must have a positive duration")`

### List response shape

```json
{
  "tasks": [
    {
      "id": 101,
      "name": "market-research",
      "task_kind": "one_shot",
      "status": "pending",
      "next_run_at": "2026-04-12T14:00:00Z",
      "trigger_type": "agent_send",
      "created_by": "agent-main"
    }
  ],
  "total": 1
}
```

Minimal shape — enough for LLM to decide next action (update/delete/get detail). Full `trigger_payload` only returned by `get` operation.

### wire_scheduler

```python
def wire_scheduler(
    self,
    schedule_task_uc: ISchedulerUseCase,
    user_timezone: str,
) -> None:
    """Phase-3 wiring. No-op if scheduler disabled or already wired."""
```

Idempotency guard: `self._scheduler_wired: bool = False` (same pattern as `_delegation_wired`).

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `parse_schedule` -- relative, ISO, edge cases | Pure function, pytest parametrize |
| Unit | `SchedulerTool.execute` -- all 5 operations, error paths | Mock `ISchedulerUseCase`, verify dispatch and ToolResult shape |
| Unit | Guardrail in `create_task` -- count >= 21 raises | Mock `count_active_by_agent` return value |
| Unit | `wire_scheduler` idempotency | Call twice, assert tool registered once |
| Integration | SQLite `created_by` migration + `count_active_by_agent` | Real SQLite in-memory DB |

## Migration / Rollout

SQLite migration: `ALTER TABLE scheduled_tasks ADD COLUMN created_by TEXT DEFAULT ''` in `ensure_schema`. Wrapped in `try/except` catching "duplicate column" (existing codebase pattern). Existing rows get `created_by = ""` -- they don't count against any agent's guardrail cap.

No feature flags needed. The tool is registered globally when `scheduler.enabled = True` (existing config). Rollback: remove the tool registration in `wire_scheduler` -- the column stays harmless.

## Resolved Questions

1. **Cron + relative disambiguation**: Reject `+` prefix on `task_kind="recurring"` at tool layer. Invalid combination by definition.
2. **`+0m` semantics**: Reject zero-duration relative offsets at tool layer. Input validation, not business logic.
3. **List serialisation format**: `{"tasks": [...], "total": N}` with minimal per-task fields. Full detail via `get`.
