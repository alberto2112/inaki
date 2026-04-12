# Proposal: scheduler-llm-tool

## Intent

Inaki's scheduler infrastructure is fully built — `ScheduledTask` entities, `ScheduleTaskUseCase` with full CRUD, SQLite persistence, dispatch adapters for `channel_send`, `agent_send`, and `shell_exec` — but it is entirely CLI-gated. The LLM cannot autonomously schedule, inspect, modify, or cancel tasks. This change exposes the built-in scheduler to the LLM as a single multi-operation tool, enabling autonomous task scheduling. Use cases range from simple reminders ("recordame a las 14h que tengo que llamar") to complex one-shot delegations ("esta noche a las 2am hacé investigación de mercado y mandámelo por email") to recurring shell executions.

## Scope

### In Scope

1. **New `SchedulerTool`** at `adapters/outbound/tools/scheduler_tool.py` — single `ITool` with `operation` enum parameter (`create`, `list`, `get`, `update`, `delete`). Constructor-injected with `ScheduleTaskUseCase`, `agent_id`, and `user_timezone`. Keeps the tool registry clean (one entry, not five).

2. **`created_by` field on `ScheduledTask`** — new `created_by: str = ""` field on the domain entity. Tracks which agent created a task. Required for the per-agent guardrail. SQLite migration via `ALTER TABLE ... ADD COLUMN created_by TEXT DEFAULT ''` for backward compatibility with existing rows.

3. **`user.timezone` in config** — new `UserConfig(timezone: str = "UTC")` sub-model added to `GlobalConfig` as `user: UserConfig`. Surfaced in the tool description so the LLM knows the user's local timezone when computing ISO 8601 datetimes from natural language like "tonight at 2am".

4. **Max active tasks guardrail** — `ScheduleTaskUseCase.create_task` checks count of active (non-completed, non-failed) tasks where `created_by == requesting_agent_id`. Raises `TooManyActiveTasksError` if count >= 21. New domain error in `core/domain/errors.py`.

5. **Two-phase wiring** — `AgentContainer.wire_scheduler(schedule_task_uc, user_timezone)` method, called from `AppContainer` after all `AgentContainer` instances are constructed. Follows the proven `wire_delegation()` pattern. Registers the `SchedulerTool` in the agent's tool executor. No-op if scheduler is disabled.

6. **`count_active_by_agent` on repository port** — new method on `ISchedulerRepository` and its SQLite implementation. Efficient `SELECT COUNT(*)` with `WHERE created_by = ? AND status NOT IN ('completed', 'failed')`.

7. **Config example update** — `config/global.example.yaml` updated with `user.timezone` field documentation.

### Out of Scope

- **IoT triggers** — no IoT extension exists today; not in immediate plans.
- **NLP/natural-language time parsing** — dual format (relative `+Xh` + ISO 8601) covers all cases. No chrono/dateparser dependency.
- **Rate limiting beyond 21-cap** — no execution frequency limits, no horizon cap, no cooldown.
- **`consolidate_memory` trigger** — internal-only; not exposed to the LLM.
- **Per-agent visibility restrictions** — full CRUD across agents. `BuiltinTaskProtectedError` (id < 100) is the only barrier.
- **Scheduler enable/disable per agent** — scheduler access is global; if the scheduler is enabled system-wide, all agents can use the tool. Per-agent gating can be added later as a delta.
- **Tool-level cron expression validation** — the tool passes through to the use case; invalid cron expressions fail at dispatch time (existing behavior).

## Capabilities

### New Capabilities

- `scheduler-llm-tool`: LLM can create, list, get, update, and delete scheduled tasks via a single tool. Supports all three exposed trigger types (`channel_send`, `agent_send`, `shell_exec`). Guardrailed at 21 active tasks per agent.

### Modified Capabilities

- `scheduler-internal`: Extended with `created_by` authorship tracking and per-agent active task count. No behavioral change to existing dispatch or execution logic.

## Approach

### Single multi-operation tool

One `SchedulerTool` with an `operation` string enum parameter. The LLM selects the operation and provides the relevant parameters. This mirrors the pattern of complex tools that multiplex operations through a single entry point rather than polluting the tool registry with five separate tools.

Operations:
- **`create`**: requires `name`, `task_kind`, `trigger_type`, `trigger_payload`, `schedule`. Optional: `description`, `executions_remaining`.
- **`list`**: no required params. Returns all tasks (optionally filtered by status in future).
- **`get`**: requires `task_id`.
- **`update`**: requires `task_id` + any updateable fields.
- **`delete`**: requires `task_id`.

### Dual time format (relative + ISO 8601)

For one-shot tasks, the `schedule` field accepts two formats:
- **Relative** (starts with `+`): `+5h`, `+2d23h5m`, `+30m`. The tool calculates the absolute datetime from `now()`. Discriminator: `schedule[0] == '+'`. This is the primary strategy — it eliminates the need for the LLM to know current time or timezone.
- **ISO 8601** (fallback): `2026-04-12T14:00:00-03:00`. For cases where the user specifies an absolute datetime. Requires the LLM to have datetime context (e.g., from system prompt).

For recurrent tasks, `cron_expression` is unchanged — no impact from this decision.

`user.timezone` is still added to config (needed for ISO 8601 fallback and potentially for system prompt injection), but the tool description does NOT dynamically regenerate with current datetime. Timezone/datetime context in the system prompt is a separate concern outside this change's scope.

### Constructor injection for agent_id

`_tool_loop.py` does NOT pass `agent_id` to `tool.execute()` — tools are stateless kwargs receivers. The `SchedulerTool` receives `agent_id` at construction time (same pattern as `DelegateTool` receiving `allowed_targets` and `get_agent_container`). The tool then passes `agent_id` as `created_by` on every `create` call and uses it for the guardrail count.

### Two-phase wiring

`ScheduleTaskUseCase` is an `AppContainer` singleton. `AgentContainer` is constructed before the use case is available for injection. Solution: `AgentContainer.wire_scheduler(schedule_task_uc, user_timezone)` is called in a second pass after all containers are built — identical to `wire_delegation()`. An idempotency guard prevents double-wiring.

### SQLite migration

`created_by` column added via `ALTER TABLE scheduled_tasks ADD COLUMN created_by TEXT DEFAULT ''`. This is safe for existing rows (they get empty string, meaning "created via CLI" or "unknown origin"). The guardrail counts only tasks where `created_by` matches the requesting agent, so legacy tasks with `created_by = ""` don't count against any agent's cap.

## Failure Modes

| Failure | ToolResult |
|---|---|
| Unknown operation | `success: false`, error describing valid operations |
| `task_id` not found | `success: false`, `TaskNotFoundError` message |
| Builtin task (id < 100) modification/deletion | `success: false`, `BuiltinTaskProtectedError` message |
| Max active tasks exceeded (>= 21) | `success: false`, `TooManyActiveTasksError` message |
| Invalid trigger payload | `success: false`, Pydantic validation error message |
| Invalid schedule (bad cron / bad ISO datetime) | `success: false`, validation error message |

All failures return normally to the LLM as `ToolResult(success=False, error=...)`. The LLM decides next action. No automatic retries — consistent with every other tool in Inaki.

## Affected Areas

### Files to Create

| File | Purpose |
|---|---|
| `adapters/outbound/tools/scheduler_tool.py` | `SchedulerTool` implementing `ITool` with operation enum |

### Files to Modify

| File | Change |
|---|---|
| `core/domain/entities/task.py` | Add `created_by: str = ""` field to `ScheduledTask` |
| `core/domain/errors.py` | Add `TooManyActiveTasksError` |
| `core/ports/outbound/scheduler_port.py` | Add `count_active_by_agent(agent_id: str) -> int` method |
| `core/use_cases/schedule_task.py` | Guardrail in `create_task`: count active by agent, raise if >= 21 |
| `adapters/outbound/scheduler/sqlite_scheduler_repo.py` | `created_by` column migration + `count_active_by_agent` impl |
| `infrastructure/config.py` | Add `UserConfig(timezone: str = "UTC")` + `GlobalConfig.user` |
| `infrastructure/container.py` | `AgentContainer.wire_scheduler()` + `AppContainer` second-pass call |
| `config/global.example.yaml` | Document `user.timezone` field |

### Files Unchanged

- `core/use_cases/run_agent.py` — no changes needed; tool loop is agnostic.
- `core/use_cases/_tool_loop.py` — no changes; `agent_id` is constructor-injected, not passed at execute time.
- `adapters/outbound/scheduler/dispatch_adapters.py` — dispatch logic untouched; `created_by` is metadata, not dispatch concern.
- All existing tests — no behavioral regressions expected.

## Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **LLM date arithmetic errors** — LLMs are weak at temporal reasoning. Risk is limited to the ISO 8601 fallback path; the primary relative format (`+5h`) requires no date arithmetic. | Low | Relative format (`+Xh`, `+Xd`) is the primary strategy — no datetime knowledge needed. ISO 8601 is fallback only. Can add validation (reject past datetimes for one-shot) as a cheap guardrail. |
| **SQLite migration on existing databases** — `ALTER TABLE ADD COLUMN` is safe in SQLite, but if the column already exists from a failed prior migration, it will error. | Low | Use `try/except` around the ALTER TABLE, catching "duplicate column" errors silently. Existing pattern in the codebase. |
| **Two-phase wiring complexity** — Adding a second `wire_*` method increases `AppContainer` init complexity. | Low | Pattern is proven by `wire_delegation()`. Document the ordering contract. Idempotency guard prevents double-wiring. |
| **Tool description length** — A single tool with 5 operations and 3 trigger types produces a large `parameters_schema`. May consume significant context. | Low | Keep descriptions concise. The discriminated union for trigger payloads is already modeled in the domain — reuse the same structure in the JSON schema. |

## Alternatives Considered

### NLP time parsing vs ISO 8601 vs Dual format

**NLP** (dateparser/chrono): User says "tonight at 2am", library parses to datetime. Pro: tolerant of fuzzy input. Con: adds a dependency, ambiguous edge cases (which "tonight"?), locale issues, and the LLM would still need to formulate the NLP input string.

**ISO 8601 only**: LLM computes the datetime given `user.timezone` and current time. Pro: zero dependencies, unambiguous. Con: LLM date arithmetic can fail; requires injecting datetime context somewhere (tool description or system prompt).

**Dual format — relative + ISO 8601** (chosen): Primary strategy is relative (`+5h`, `+2d23h5m`) — the tool calculates absolute datetime from `now()`. Discriminator: `schedule[0] == '+'`. ISO 8601 is the fallback for absolute times. Pro: LLM needs no datetime/timezone context for the common case; zero dependencies; trivial parsing. Con: relative format can't express "next Tuesday at 9am" directly (user would use ISO fallback).

Decision: Dual format — best of both worlds. Relative covers 80%+ of one-shot use cases without any datetime context. ISO 8601 fallback for the rest.

### Single multi-op tool vs multiple tools

**Multiple tools** (`create_task`, `list_tasks`, `get_task`, `update_task`, `delete_task`): Pro: each tool has a focused schema. Con: pollutes the tool registry with 5 entries; RAG-based tool selection must surface the right one; conceptually they are one capability.

**Single tool with operation enum** (chosen): Pro: one registry entry, clean semantics, LLM picks operation via parameter. Con: larger schema, slightly more complex dispatch inside the tool.

Decision: Single tool — cleaner registry, the LLM handles operation selection well, and the schema is self-documenting.

### Enable/disable per agent vs global

**Per-agent**: Each agent has `scheduler.enabled: true/false` in its config, like delegation. Pro: fine-grained control. Con: more config surface, more wiring logic, and the user explicitly chose "full CRUD across agents" with no restrictions.

**Global** (chosen): If the system scheduler is enabled, all agents get the tool. Pro: simple, matches user's "no restrictions between agents" decision. Con: cannot restrict scheduler access per agent.

Decision: Global — matches user intent. Per-agent gating is a straightforward delta if needed later.
