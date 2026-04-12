# Tasks: agent-delegation

## Ordering rationale

Bottom-up: domain errors and data contracts first, then the tool-loop extraction (foundation other phases depend on), then the one-shot use case, then the delegation tool, then config, then container wiring, then prompt injection, then tests, then docs. No consumer is created before its producer.

## Tasks

### Phase 1 — Foundation (no breakage possible)

- [ ] 1.1 — Add `ToolLoopMaxIterationsError` to `core/domain/errors.py`
  - Description: New domain exception. `ToolLoopMaxIterationsError(last_response: str)` stores the last LLM response for the caller to return as fallback.
  - Files: `core/domain/errors.py` (modify)
  - Tests: `tests/unit/domain/test_errors.py` (new) — instantiation and attribute access.
  - Dependencies: none
  - Estimated diff: S

- [ ] 1.2 — Create `core/use_cases/_tool_loop.py` with extracted loop logic
  - Description: Extract `_run_with_tools` from `run_agent.py` into `run_tool_loop(*, llm, tools, messages, system_prompt, tool_schemas, max_iterations, circuit_breaker_threshold, agent_id) -> str`. Raises `ToolLoopMaxIterationsError` on breach. Helper is delegation-agnostic — no ContextVar, no depth concept, no delegation knowledge.
  - Files: `core/use_cases/_tool_loop.py` (new)
  - Tests: `tests/unit/use_cases/test_tool_loop.py` (new) — happy path, max_iterations raises `ToolLoopMaxIterationsError`, circuit breaker trips.
  - Dependencies: 1.1
  - Estimated diff: M

- [ ] 1.3 — Refactor `run_agent.py` to use `run_tool_loop`
  - Description: Replace both `_run_with_tools` call sites with `await run_tool_loop(...)`. Catch `ToolLoopMaxIterationsError` and return `e.last_response` (preserves silent fallthrough). **Risk**: missing catch breaks all 13 existing tests.
  - Files: `core/use_cases/run_agent.py` (modify)
  - Tests: `tests/unit/use_cases/test_run_agent_basic.py` — all 13 must still pass unchanged.
  - Dependencies: 1.2
  - Estimated diff: S

### Phase 2 — One-shot execution capability

- [ ] 2.1 — Create `DelegationResult` dataclass in `core/domain/value_objects/delegation_result.py`
  - Description: Pydantic model with `status: str`, `summary: str`, `details: str | None`, `reason: str | None`. Serializable to dict/JSON.
  - Files: `core/domain/value_objects/delegation_result.py` (new)
  - Tests: `tests/unit/domain/test_delegation_result.py` (new) — field validation, optional fields.
  - Dependencies: none
  - Estimated diff: S

- [ ] 2.3 — Add `extra_sections` parameter to `AgentContext.build_system_prompt`
  - Description: `build_system_prompt(self, base_prompt: str, extra_sections: list[str] | None = None) -> str`. When provided, concatenates each section after the base prompt. Non-breaking default.
  - Files: `core/domain/value_objects/agent_context.py` (modify)
  - Tests: `tests/unit/domain/test_agent_context.py` (new or extend) — None produces no change, single section appended, multiple sections appended in order.
  - Dependencies: none
  - Estimated diff: S

- [ ] 2.4 — Create `core/use_cases/run_agent_one_shot.py`
  - Description: `RunAgentOneShotUseCase` with `execute(task, system_prompt, max_iterations, timeout_seconds) -> str`. Uses `tool_registry.get_schemas()` (full list, no RAG) and builds system_prompt from the agent's default or from override. Wraps `run_tool_loop` in `asyncio.wait_for`. Raises `asyncio.TimeoutError` and `ToolLoopMaxIterationsError` (callers map these). Does NOT load/persist history, does NOT read digest. **Filters out the `"delegate"` tool from the schemas list before passing to `run_tool_loop`** — recursion prevention by construction (REQ-DG-9).
  - Files: `core/use_cases/run_agent_one_shot.py` (new)
  - Tests: `tests/unit/use_cases/test_run_agent_one_shot.py` (new) — REQ-OS-1 (no history), REQ-OS-2 (prompt override), REQ-OS-3 (max_iter raises, timeout raises), REQ-OS-4 (one-shot passes full `get_schemas()` result, RAG is not called), REQ-DG-9 (delegate tool absent from schemas passed to child).
  - Dependencies: 1.2, 2.3
  - Estimated diff: M

### Phase 3 — Delegation tool

- [ ] 3.1 — Create `core/use_cases/_result_parser.py` with trailing JSON block parser
  - Description: `parse_delegation_result(text: str) -> DelegationResult`. Extracts last ` ```json ... ``` ` block, validates fields. Returns `DelegationResult(status="failed", reason="result_parse_error", details=text)` on any failure.
  - Files: `core/use_cases/_result_parser.py` (new)
  - Tests: `tests/unit/use_cases/test_result_parser.py` (new) — REQ-DG-4 happy path, REQ-DG-5 no block, REQ-DG-5 invalid JSON, block not last in message.
  - Dependencies: 2.1
  - Estimated diff: S

- [ ] 3.2 — Create `adapters/outbound/tools/delegate_tool.py`
  - Description: `DelegateTool(ITool)`. Final tool signature: `execute(agent_id: str, task: str, system_prompt: str | None = None)`. Validates: allow-list check → `target_not_allowed`; registry lookup → `unknown_agent`. Calls `RunAgentOneShotUseCase`, wraps with `asyncio.wait_for` for timeout. Catches `TimeoutError` → `timeout`, `ToolLoopMaxIterationsError` → `max_iterations_exceeded`, `Exception` → `child_exception:<Type>`. Parses result via `_result_parser`. NEVER raises — always returns `DelegationResult` serialized to JSON string as `ToolResult.output`. No depth management — recursion prevention is handled by `RunAgentOneShot` filtering the `"delegate"` tool from child schemas.
  - Files: `adapters/outbound/tools/delegate_tool.py` (new)
  - Tests: `tests/unit/adapters/tools/test_delegate_tool.py` (new) — REQ-DG-2, REQ-DG-3, REQ-DG-4, REQ-DG-5, REQ-DG-6, REQ-DG-8 scenarios; canonical reason strings must match design table exactly.
  - Dependencies: 2.1, 2.4, 3.1
  - Estimated diff: M

### Phase 4 — Config extension

- [ ] 4.1 — Extend `infrastructure/config.py` with `DelegationConfig` (global) and per-agent `delegation` section
  - Description: Global `DelegationConfig(max_iterations_per_sub: int = 10, timeout_seconds: int = 60)`. Per-agent `AgentDelegationConfig(enabled: bool = False, allowed_targets: list[str] = [])`. Add `delegation: DelegationConfig` to `GlobalConfig`; add `delegation: AgentDelegationConfig` to `AgentConfig`. No `max_depth` field — recursion prevention is by construction, not config.
  - Files: `infrastructure/config.py` (modify)
  - Tests: `tests/unit/infrastructure/test_config.py` (new or extend) — defaults (`enabled`, `allowed_targets`, `max_iterations_per_sub`, `timeout_seconds`), YAML override for each field, missing section uses defaults.
  - Dependencies: none
  - Estimated diff: S

### Phase 5 — Container wiring

- [ ] 5.1 — Add `wire_delegation(get_agent_container)` to `AgentContainer` and two-phase init to `AppContainer`
  - Description: `AgentContainer` stashes `global_config` and `registry` refs in `__init__`. `wire_delegation` is a no-op when `delegation.enabled` is False; otherwise instantiates `RunAgentOneShotUseCase` + `DelegateTool` and registers the tool. `AppContainer.__init__` adds Phase 2 loop after existing Phase 1 build loop.
  - Files: `infrastructure/container.py` (modify)
  - Tests: `tests/unit/infrastructure/test_container.py` (new or extend) — REQ-DG-1 (delegate tool registered when enabled, absent when disabled), wiring idempotent when called twice.
  - Dependencies: 3.2, 4.1
  - Estimated diff: M

### Phase 6 — Prompt injection

- [ ] 6.1 — Inject agent-discovery section into parent system prompt when delegation enabled
  - Description: In `wire_delegation`, build an agent-discovery section listing available target agents (id, description, tool names) filtered by `allowed_targets`. Pass as `extra_sections` when calling `AgentContext.build_system_prompt` in `RunAgentUseCase`. REQ-DG-9 scenarios.
  - Files: `infrastructure/container.py` (modify), `core/use_cases/run_agent.py` (modify to thread extra_sections)
  - Tests: `tests/unit/infrastructure/test_container.py` (extend) — REQ-DG-9: section present when enabled, filtered by allow-list, absent when disabled.
  - Dependencies: 5.1, 2.3
  - Estimated diff: S

- [ ] 6.2 — Inject result-format footer into child system prompt when running one-shot for delegation
  - Description: `DelegateTool.execute` constructs a `system_prompt` that appends the result-format instruction to the child's default prompt, instructing the child to emit a trailing ` ```json ``` ` block with `status`, `summary`, `details`, `reason`. Child thus knows the contract without polluting its base config. The override is passed via the `system_prompt` parameter of `RunAgentOneShotUseCase.execute`.
  - Files: `adapters/outbound/tools/delegate_tool.py` (modify)
  - Tests: `tests/unit/adapters/tools/test_delegate_tool.py` (extend) — verify the system_prompt passed to `RunAgentOneShotUseCase` contains the format instruction.
  - Dependencies: 3.2, 2.4
  - Estimated diff: S

### Phase 7 — Tests

- [ ] 7.1 — Integration test: end-to-end happy-path delegation (parent → child → structured result)
  - Description: Mocked LLM. Parent calls `delegate`, child produces valid JSON block. Assert `DelegationResult.status == "success"`.
  - Files: `tests/unit/use_cases/test_delegation_integration.py` (new)
  - Tests: REQ-DG-4 full round-trip.
  - Dependencies: 5.1, 6.2
  - Estimated diff: M

- [ ] 7.2 — Integration tests: all failure modes
  - Description: `unknown_agent`, `target_not_allowed`, `result_parse_error` (no block + invalid JSON), `timeout`, `max_iterations_exceeded`, `child_exception`. Each maps to canonical reason string from design table.
  - Files: `tests/unit/use_cases/test_delegation_integration.py` (extend)
  - Tests: REQ-DG-2, REQ-DG-3, REQ-DG-5, REQ-DG-6, REQ-DG-8 (failure modes).
  - Dependencies: 7.1
  - Estimated diff: M

- [ ] 7.3 — Integration test: sub-agent schema excludes delegate tool
  - Description: Verify that when a parent delegates to a child, the child's `run_tool_loop` call receives a schemas list that does NOT include the `"delegate"` tool, even when the child agent has `delegation.enabled: true`. REQ-DG-9.
  - Files: `tests/unit/use_cases/test_delegation_integration.py` (extend)
  - Tests: REQ-DG-9 scenario.
  - Dependencies: 7.1
  - Estimated diff: S

### Phase 8 — Documentation & examples

- [ ] 8.1 — Update `config/global.example.yaml` with `delegation` section (commented)
  - Description: Add commented block showing `delegation.max_iterations_per_sub`, `timeout_seconds` with defaults. No `max_depth` — recursion prevention is by construction.
  - Files: `config/global.example.yaml` (modify)
  - Tests: none
  - Dependencies: 4.1
  - Estimated diff: S

- [ ] 8.2 — Add minimal example agent config with `delegation.enabled: true`
  - Description: New example file showing a coordinator agent with `delegation.enabled: true` and optional `allowed_targets`.
  - Files: `config/agents/coordinator.example.yaml` (new)
  - Tests: none
  - Dependencies: 4.1
  - Estimated diff: S

---

## Critical path

**Minimum for a working happy-path delegation:**

`1.1 → 1.2 → 1.3 → 2.1 → 2.3 → 2.4 → 3.1 → 3.2 → 4.1 → 5.1 → 6.1 → 6.2`

(12 tasks)

## Parallelization opportunities

- **1.1, 2.1, 2.3, 4.1** — no dependencies on each other, can run in parallel as the very first batch.
- **Phase 7 tests (7.1, 7.2, 7.3)** — 7.1 must complete first; 7.2 and 7.3 can run in parallel after 7.1.
- **8.1, 8.2** — fully independent, can run any time after 4.1.
- **3.1** can start as soon as 2.1 is done, in parallel with 2.4.
- **3.2** depends on 2.1, 2.4, 3.1 only.

## Definition of done

- [ ] All 13 requirements from spec (REQ-OS-1…4 + REQ-DG-1…9) are covered by tests
- [ ] All spec scenarios have a corresponding test
- [ ] All 13 existing `test_run_agent_basic.py` tests still pass
- [ ] `config/global.example.yaml` updated with `delegation` section
- [ ] No tool-loop duplication — single source of truth in `core/use_cases/_tool_loop.py`
- [ ] Canonical reason strings match design table exactly (no `malformed_result`, no bare `max_iterations`, no `max_depth_exceeded`, no `unknown_tool`)
- [ ] Sub-agents do not have access to the `delegate` tool in their execution context (verified by test 7.3)
- [ ] `DelegateTool.execute` never raises — always returns `DelegationResult`
- [ ] `ToolLoopMaxIterationsError` caught in `RunAgentUseCase` (silent fallthrough preserved)
- [ ] One-shot execution passes full tool schemas without RAG filtering (REQ-OS-4)
