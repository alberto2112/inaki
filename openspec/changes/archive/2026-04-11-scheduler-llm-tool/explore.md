# Exploration: scheduler-llm-tool

## Current State

The scheduler infrastructure is fully built but entirely CLI-gated. The LLM has no access to it today.

**What exists and works:**
- `core/domain/entities/task.py` — `ScheduledTask` entity (Pydantic) with a discriminated-union `TriggerPayload` covering `channel_send`, `agent_send`, `shell_exec`, and `consolidate_memory`. All three LLM-exposed trigger types (decision #1) are already modelled.
- `core/use_cases/schedule_task.py` — `ScheduleTaskUseCase` with full CRUD: `create_task`, `get_task`, `list_tasks`, `update_task`, `delete_task`, `enable_task`, `disable_task`. Builtin protection (`id < 100 → BuiltinTaskProtectedError`) already enforced in `delete_task` and `update_task`.
- `adapters/outbound/scheduler/sqlite_scheduler_repo.py` — SQLite persistence. User tasks auto-assigned `id >= 100` (COALESCE MAX + 1). Builtin tasks are id < 100. `seed_builtin` and `save_task` are separate paths.
- `adapters/outbound/scheduler/dispatch_adapters.py` — `ChannelSenderAdapter`, `LLMDispatcherAdapter`, `ConsolidationDispatchAdapter` wired in `AppContainer`.
- `infrastructure/container.py` — `AppContainer` builds `schedule_task_uc` and `scheduler_service` at startup. These are **app-level singletons**, not per-agent.
- Tests: `tests/unit/use_cases/test_schedule_task.py` (unit, AsyncMock), `tests/integration/scheduler/` (integration).

**What does NOT exist:**
- No `scheduler_task` tool (or any scheduler tool) in `adapters/outbound/tools/`.
- `ScheduledTask` has no `created_by` / `agent_id` field — there is no authorship tracking today.
- `GlobalConfig` has no `user.timezone` field (`SchedulerConfig` has no timezone either).
- `ScheduleTaskUseCase.create_task` has no guardrail — no max-tasks check.

**Tool pattern (from `DelegateTool`, `WebSearchTool`):**
- Every tool is a class inheriting `ITool` (abstract, in `core/ports/outbound/tool_port.py`).
- Must declare: `name: str`, `description: str`, `parameters_schema: dict` (JSON Schema), and implement `async def execute(**kwargs) -> ToolResult`.
- Tools that need app-level services (like the scheduler use case) receive them via `__init__` (constructor injection — see `DelegateTool`).
- Tools are registered in `AgentContainer._register_tools()` for basic tools, or `wire_delegation()` for tools requiring cross-agent access. The scheduler tool will need a similar "late wiring" step since `schedule_task_uc` lives on `AppContainer`, not `AgentContainer`.
- `ToolRegistry.execute(tool_name, **kwargs)` dispatches by name; kwargs come straight from the LLM's JSON `arguments`. The tool's `execute` receives them as Python kwargs — the tool must validate and handle them.
- **`agent_id` is NOT passed to tools**. The tool loop (`_tool_loop.py`) passes `agent_id` only to the logger, not to `tools.execute()`. This means the scheduler tool cannot know which agent invoked it without being bound to a specific agent at construction time (constructor injection pattern, same as `DelegateTool`).

**Agent-ID authorship gap:**
`ScheduledTask` has no `created_by` field. The pre-decisions say full CRUD is allowed cross-agent (no ownership restriction), but for guardrail purposes (max 21 active tasks **per agent**), we need to know which tasks belong to which agent. Two options:
1. Add `created_by: str = ""` to `ScheduledTask` entity and the SQLite schema.
2. Inject `agent_id` at construction time into the tool; count active tasks filtered by `created_by`.

Since the guardrail is **per-agent** and the pre-decisions confirm that is the only guardrail, option 1 is required.

**`user.timezone` gap:**
`GlobalConfig` has no `user` section. `SchedulerConfig` also lacks a timezone field. The LLM needs to know the user's timezone to reason about "today at 14h" → ISO 8601 datetime. The config extension is straightforward: add `UserConfig(timezone: str = "UTC")` under `GlobalConfig.user`, add to `_render_default_global_yaml()`, and document in `config/global.example.yaml`.

**Guardrail pattern:**
No existing guardrail/cap pattern in other tools or use cases to reuse. This is the first max-count guardrail in the system. It will be implemented inside `ScheduleTaskUseCase.create_task` (or a new method): count active tasks for the requesting agent and raise a new `TooManyActiveTasksError` if `>= 21`.

## Affected Areas

- `core/domain/entities/task.py` — Add `created_by: str = ""` field to `ScheduledTask`
- `core/domain/errors.py` — Add `TooManyActiveTasksError(SchedulerError)`
- `core/ports/inbound/scheduler_port.py` — Optionally add `list_tasks_by_agent(agent_id)` or keep it a filter in the use case
- `core/ports/outbound/scheduler_port.py` — May need `count_active_by_agent(agent_id)` or reuse `list_tasks`
- `core/use_cases/schedule_task.py` — Add guardrail to `create_task`: count active tasks for agent, raise if >= 21
- `adapters/outbound/scheduler/sqlite_scheduler_repo.py` — Add `created_by` column to schema migration; add `count_active_by_agent` query
- `adapters/outbound/tools/schedule_task_tool.py` — **NEW** — `ScheduleTaskTool` implementing ITool; single tool with `operation` enum (create / list / get / update / delete / enable / disable)
- `infrastructure/container.py` — Wire `ScheduleTaskTool` into each `AgentContainer` (needs access to app-level `schedule_task_uc` + agent's own `agent_id`)
- `infrastructure/config.py` — Add `UserConfig(timezone: str = "UTC")` + `GlobalConfig.user: UserConfig`
- `config/global.example.yaml` — Document new `user.timezone` field
- Tests: `tests/unit/adapters/tools/test_schedule_task_tool.py` — **NEW**; extend `tests/unit/use_cases/test_schedule_task.py` for guardrail

## Approaches

### 1. Single multi-operation tool (`operation` enum)
One tool `schedule_task` with `operation: str` parameter (create / list / get / update / delete / enable / disable), mirroring the `WebSearchTool` pattern.
- **Pros**: Single tool schema visible to LLM; one registration point; simpler wiring; maps directly to `ScheduleTaskUseCase` methods.
- **Cons**: Large JSON schema description; LLM must pick the right `operation` string.
- **Effort**: Medium

### 2. Multiple granular tools (one per operation)
`create_scheduled_task`, `list_scheduled_tasks`, `get_scheduled_task`, `update_scheduled_task`, `delete_scheduled_task`.
- **Pros**: Cleaner per-tool descriptions; RAG-based tool selection can surface only the relevant one.
- **Cons**: More files; more registration calls; harder to maintain; the project has only 4 other tools total.
- **Effort**: Medium-High

## Recommendation

**Approach 1 (single multi-operation tool)** — consistent with `WebSearchTool` pattern already established in the project. The RAG tool selection (`get_schemas_relevant`) can still surface this tool based on scheduling intent in the user query. Simpler implementation and single entry point for the guardrail check.

The tool should be injected with:
- `schedule_task_uc: ISchedulerUseCase` (from `AppContainer`)
- `agent_id: str` (bound at wiring time in `AgentContainer`)
- `user_timezone: str` (from `GlobalConfig.user.timezone`)

## Risks

1. **Schema migration for `created_by`**: Adding a non-nullable column `created_by` to an existing `scheduled_tasks` table requires a safe migration (ALTER TABLE with DEFAULT '' or schema recreation). Production databases may already have rows; the migration must be backward compatible.
2. **LLM date arithmetic**: Per decision #2, the LLM calculates ISO 8601 datetimes. If `user.timezone` is not injected into the system prompt (or tool description), the LLM will default to UTC and produce wrong local-time scheduling. The tool or the agent's system prompt MUST make the user's timezone explicit.
3. **`AppContainer` singleton vs `AgentContainer` scoping**: `schedule_task_uc` lives on `AppContainer` (single instance, shared across all agents). Wiring it into each `AgentContainer._register_tools()` requires a two-phase init or a late-wiring step analogous to `wire_delegation()`. If wired too early (Phase 1), `AppContainer` isn't fully built yet. This must use the same two-phase pattern as delegation.

## Ready for Proposal

Yes — the architecture is clear. All infrastructure exists; the gaps are well-defined and small. The proposal phase should address: the two-phase wiring approach for the scheduler tool, the `created_by` migration strategy, and how `user.timezone` surfaces in the LLM context.
