# Proposal: agent-delegation

## Intent

Inaki runs agents as fully isolated units today — each owns its REST port, Telegram bot, workspace, tools, and history. A coordinator agent cannot leverage a specialist's capability surface without duplicating its tools and prompt. This change introduces first-class **agent-to-agent delegation** via a `delegate` tool: a parent agent hands a subtask to a sibling, the child runs one-shot (no history load/persist), and returns a structured, machine-parseable result. This unlocks composition — the foundation for Inaki's multi-agent story.

## Scope

### In Scope
- New `RunAgentOneShotUseCase` at `core/use_cases/run_agent_one_shot.py` — clean context, no history, no digest, accepts `system_prompt` + `tool_schemas` overrides.
- **Extract `_run_with_tools`** (currently `core/use_cases/run_agent.py:152-226`) into a shared helper `core/use_cases/_tool_loop.py`. Pure function: LLM client + messages + system_prompt + tool schemas + executor + max_iterations + circuit_breaker_threshold → LLM response. Both `RunAgentUseCase` and `RunAgentOneShotUseCase` delegate to it. Refactoring `run_agent.py` to consume the helper is part of this change — two real consumers, not speculative reuse.
- New `DelegateTool` at `adapters/outbound/tools/delegate_tool.py` implementing `ITool`; receives `get_agent_container` callable; validates `allowed_targets`; returns `DelegationResult`.
- `DelegationConfig` Pydantic sub-model in `infrastructure/config.py`; added to `AgentConfig` + `GlobalConfig` as optional (default disabled).
- `AgentContext.build_system_prompt` extended with `extra_sections: list[str] = []` (non-breaking).
- Post-construction wiring: `AgentContainer.wire_delegation(get_agent)` called from `AppContainer.__init__` after agent loop.
- Result parser utility `core/utils/delegation_result_parser.py` — extracts trailing \`\`\`json block.
- Domain errors: `DelegationError`, `DelegationTargetNotAllowedError`.
- Test coverage for one-shot use case, delegate tool, result parser, config, tool loop helper.

Specialist agents expose their pre-curated toolkit wholesale. `RunAgentOneShot` skips RAG-based tool selection and passes the full schema list to the child LLM — RAG is overhead without benefit on small curated toolkits.

### Out of Scope
- Parallel / fan-out delegation (multiple children concurrently).
- Async handle/await pattern (fire-and-forget with later retrieval).
- Cross-process delegation via HTTP/REST between separate Inaki instances.
- Inheriting memory context (digest, history) from parent into child.
- Structured streaming of child progress back to parent.
- Automatic retries or automatic fallback to another agent on failure.
- Recursive delegation — sub-agents cannot further delegate; this is enforced by construction, not by a runtime check. `RunAgentOneShot` excludes `DelegateTool` from the child's tool schemas, making recursive delegation impossible.
- Dynamic tool selection by parent — the parent cannot cherry-pick tools from an agent's toolkit. The specialist model precludes it by design. If finer-grained tool access is needed, create a new specialist agent.
- RAG-based tool filtering inside one-shot execution — the child uses its full toolkit.

## Capabilities

### New Capabilities
- `agent-delegation`: Parent agent delegates a subtask to a sibling via the `delegate` tool. Covers opt-in config, target allowlist, result contract, failure modes, and agent discovery injection. Recursive delegation is prevented by construction — `RunAgentOneShot` excludes the `DelegateTool` from the child's tool schemas.
- `agent-one-shot-execution`: Stateless agent execution path — no history load/persist, no digest, optional prompt/tools override. Used by delegation today; reusable for future non-delegation one-shot needs (e.g., programmatic invocation).

### Modified Capabilities
- None. `memory-digest`, `ext-user-extensions`, and `scheduler-internal` are untouched at the spec level.

## Approach

Per the exploration's recommended direction: extract the tool loop into a shared private helper, add a dedicated one-shot use case, and introduce `DelegateTool` with post-construction wiring. Recursive delegation is prevented by construction — `RunAgentOneShot` excludes `DelegateTool` from the tool schemas passed to the child LLM, so the child literally cannot call `delegate` regardless of what its LLM attempts. The parent discovers available siblings through a dynamically-generated system_prompt section (injected via `extra_sections` on `AgentContext.build_system_prompt`, built from `AgentRegistry` at execution time). The child's final LLM response carries a trailing \`\`\`json block with `status`, `summary`, `details`, `reason` — parsed with regex fallback to `status: "failed"`. The `delegate` tool has a minimal three-parameter signature: `delegate(agent_id, task, system_prompt?)`. Delegation is opt-in per agent via `delegation.enabled: true` in YAML.

## Failure modes

| Failure | DelegationResult |
|---|---|
| Unknown `agent_id` | `status: "failed"`, `reason: "unknown_agent"` |
| Target not in `allowed_targets` | `status: "failed"`, `reason: "target_not_allowed"` |
| Delegation disabled on parent | `status: "failed"`, `reason: "delegation_disabled"` |
| Child raises exception | `status: "failed"`, `reason: "child_exception"`, `details: <message>` |
| Child exceeds `max_iterations_per_sub` | `status: "failed"`, `reason: "max_iterations_exceeded"` |
| Child exceeds `timeout_seconds` | `status: "failed"`, `reason: "timeout"` |
| Result JSON block missing/malformed | `status: "failed"`, `reason: "result_parse_error"`, `details: <raw text>` |

All failures return normally to the parent LLM as tool results. The parent decides next action (retry with different args, try another agent, give up, report to user). **No automatic retries, no automatic fallback** — consistent with every other tool in Inaki.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `core/use_cases/run_agent.py` | Modified | Refactored to delegate tool loop to `_tool_loop.py` helper |
| `core/use_cases/_tool_loop.py` | New | Shared tool loop helper (extracted from `run_agent.py:152-226`) |
| `core/use_cases/run_agent_one_shot.py` | New | One-shot use case — no history, no digest |
| `core/domain/value_objects/agent_context.py` | Modified | `build_system_prompt` accepts `extra_sections` |
| `core/domain/errors.py` | Modified | New delegation error types |
| `core/utils/delegation_result_parser.py` | New | Trailing JSON block extractor |
| `adapters/outbound/tools/delegate_tool.py` | New | `DelegateTool` implementation |
| `adapters/outbound/tools/tool_registry.py` | Unchanged | No modifications required |
| `infrastructure/config.py` | Modified | `DelegationConfig` sub-model, added to `AgentConfig` + `GlobalConfig` |
| `infrastructure/container.py` | Modified | `wire_delegation` on `AgentContainer`; post-construction wiring in `AppContainer` |
| `tests/unit/use_cases/test_tool_loop.py` | New | Tests for extracted helper |
| `tests/unit/use_cases/test_run_agent_one_shot.py` | New | Tests for one-shot path |
| `tests/unit/tools/test_delegate_tool.py` | New | Tests for delegate tool + depth + allowlist |
| `tests/unit/utils/test_delegation_result_parser.py` | New | Parser tests |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Circular dependency in container wiring | Med | Post-construction `wire_delegation` pattern — agents built first, delegation wired after |
| LLM doesn't emit trailing JSON block | Med | Parser fallback to `status: "failed"` with raw text in `details`; parent LLM handles as normal failure |
| Tool loop extraction breaks existing 13 tests | Low | Helper is a pure function; `run_agent.py` keeps same external contract; tests mock at use-case boundary |
| Specialist agent has too many tools for effective LLM use | Low | Toolkit curation is a design-time responsibility; each specialist agent should be configured with a focused, purposeful tool set |

## Rollback Plan

Delegation is opt-in per agent (`delegation.enabled: true`, default **false**). To rollback: flip the flag to `false` in affected agent YAMLs — no code deployment needed. For a full code rollback, revert the change in git; the tool loop helper extraction is internally pure (no external API change), so `run_agent.py` continues to work after reverting `_tool_loop.py` and the one-shot use case. Existing 13 tests in `test_run_agent_basic.py` act as the regression guard for the refactor.

## Dependencies

- None external. All pieces are in-house:
  - Existing `AgentRegistry`, `AppContainer`, `ToolRegistry`, Pydantic config
  - Existing test infrastructure (`AsyncMock`, `MagicMock`)

## Success Criteria

- [ ] An agent configured with `delegation.enabled: true` and `allowed_targets: [specialist]` can call `delegate(agent_id="specialist", task="...")` (three-parameter form: `agent_id`, `task`, optional `system_prompt`) and receive a structured `DelegationResult` — proven by integration test.
- [ ] Child agent execution does not load or persist history (verified by mock assertions on `history.load` / `history.append` not being called in one-shot path).
- [ ] Parent LLM sees delegation failures as normal tool results and can react (verified by test where delegation fails and LLM continues the loop).
- [ ] `run_agent.py:152-226` is removed; both `RunAgentUseCase` and `RunAgentOneShotUseCase` call `_tool_loop.run(...)`.
- [ ] The existing 13 tests in `test_run_agent_basic.py` pass unchanged after the refactor.
- [ ] An agent with `delegation.enabled: false` (default) has no `delegate` tool in its schemas — confirmed by tool registry inspection.
- [ ] Sub-agents running via one-shot execution do not have the `delegate` tool in their schemas — confirmed by inspecting the schemas passed to `RunAgentOneShot`.
- [ ] System prompt injection: when a parent executes with delegation enabled, its system_prompt includes an "Available agents" section listing sibling IDs + descriptions, generated from `AgentRegistry` at execution time.

## Rollout

1. Merge with `delegation.enabled: false` as the default across all existing agent configs — **zero behavior change**.
2. Enable on one test agent (e.g., a coordinator in the dev environment) with a narrow `allowed_targets` list.
3. Run integration tests covering: happy path, unknown target, child exception, malformed result, sub-agent schema exclusion.
4. Gradually enable on production coordinator agents as use cases emerge.
5. Monitor via existing logging (tool executions already logged through the tool registry).

No feature flag beyond the per-agent YAML opt-in. Rollback is flipping the flag.
