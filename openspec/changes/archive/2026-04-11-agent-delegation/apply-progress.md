# Apply Progress: agent-delegation

## Completed tasks

- [x] 1.1 — `ToolLoopMaxIterationsError` in `core/domain/errors.py`
- [x] 1.2 — Create `core/use_cases/_tool_loop.py` with extracted loop logic
- [x] 1.3 — Refactor `run_agent.py` to use `run_tool_loop`
- [x] 2.1 — `DelegationResult` in `core/domain/value_objects/delegation_result.py`
- [x] 2.3 — `extra_sections` parameter on `AgentContext.build_system_prompt`
- [x] 2.4 — Create `core/use_cases/run_agent_one_shot.py`
- [x] 3.1 — Create `core/use_cases/_result_parser.py`
- [x] 3.2 — Create `adapters/outbound/tools/delegate_tool.py`
- [x] 4.1 — Extend `infrastructure/config.py` with delegation config
- [x] 5.1 — `wire_delegation` in `AgentContainer` + two-phase init in `AppContainer`
- [x] 6.1 — Inject agent-discovery section into parent system prompt
- [x] 6.2 — Inject result-format footer into child system prompt
- [x] 7.1 — Integration test: happy-path delegation
- [x] 7.2 — Integration tests: all failure modes
- [x] 7.3 — Integration test: sub-agent schema excludes delegate tool
- [x] 8.1 — Update `config/global.example.yaml` with `delegation` section
- [x] 8.2 — Add minimal example agent config with `delegation.enabled: true`

## Files touched

### Batch 1 (tasks 1.1, 2.1, 2.3, 4.1)
- `core/domain/errors.py` — added `ToolLoopMaxIterationsError(last_response: str)` inheriting `IñakiError`
- `core/domain/value_objects/delegation_result.py` — new pydantic v2 model with `status`, `summary`, `details`, `reason`
- `core/domain/value_objects/agent_context.py` — added `extra_sections: list[str] | None = None` parameter to `build_system_prompt`
- `infrastructure/config.py` — added `DelegationConfig`, `AgentDelegationConfig` models; wired into `GlobalConfig`, `AgentConfig`, `load_global_config`, `load_agent_config`

### Batch 2 (tasks 1.2, 3.1)
- `core/use_cases/_tool_loop.py` — new module; `run_tool_loop(*, llm, tools, messages, system_prompt, tool_schemas, max_iterations, circuit_breaker_threshold, agent_id) -> str`; raises `ToolLoopMaxIterationsError` on breach
- `core/use_cases/_result_parser.py` — new module; `parse_delegation_result(text: str) -> DelegationResult`; extracts last ```json block, never raises

### Batch 3 (tasks 1.3, 2.4)
- `core/use_cases/run_agent.py` — removed `_run_with_tools` and the duplicate `_extract_tool_calls`; replaced the call site in `execute()` with `await run_tool_loop(...)` (kwargs-only) wrapped in `try/except ToolLoopMaxIterationsError` → returns `e.last_response` preserving silent fallthrough; removed unused `json`, `field` imports; added `run_tool_loop` + `ToolLoopMaxIterationsError` imports
- `core/use_cases/run_agent_one_shot.py` — new; `RunAgentOneShotUseCase` constructor injects `(llm, tools, agent_config)`; `execute(task, system_prompt, max_iterations, timeout_seconds) -> str` wraps `run_tool_loop` in `asyncio.wait_for`; filters `"delegate"` tool from schemas list (REQ-DG-9 recursion prevention by construction); does not load/persist history, does not read digest, uses full `tool_registry.get_schemas()` (no RAG)

### Batch 4 (task 3.2)
- `adapters/outbound/tools/delegate_tool.py` — new; `DelegateTool(ITool)` with `name="delegate"`. Constructor injects `allowed_targets`, `get_agent_container` callable, `max_iterations_per_sub`, `timeout_seconds`. `execute(agent_id, task, system_prompt=None)` flow: allow-list check → registry lookup → retrieve `container.run_agent_one_shot` → call child with try/except catching `asyncio.TimeoutError` → `reason="timeout"`, `ToolLoopMaxIterationsError` → `reason="max_iterations_exceeded"`, `Exception` → `reason="child_exception:<Type>"`. On success, `parse_delegation_result(raw)` builds the `DelegationResult`. Always returns `ToolResult(output=result.model_dump_json())` — NEVER raises. Hook point for 6.2 marked at effective-prompt construction step.
- `tests/unit/adapters/tools/__init__.py` — new package init

### Batch 5 (tasks 5.1, 8.1, 8.2)
- `infrastructure/container.py` — `AgentContainer.__init__` stashes `self._global_config` and `self._delegation_wired: bool = False`. New method `wire_delegation(get_agent_container)`: no-op when `delegation.enabled is False`, idempotent via `_delegation_wired` flag, otherwise builds `RunAgentOneShotUseCase(llm, tools, agent_config)` → assigns to `self.run_agent_one_shot` (LOAD-BEARING name), builds `DelegateTool(allowed_targets, get_agent_container, max_iterations_per_sub, timeout_seconds)`, registers in tool registry. `AppContainer.__init__` adds Phase 2 loop after existing Phase 1: iterates all built containers, passes a `_get_agent_container` closure over `self.agents` (uses `self.agents.get(agent_id)` → returns `None` on miss, matching `DelegateTool`'s `Callable[[str], AgentContainer | None]` contract — NOT `AppContainer.get_agent` which raises `AgentNotFoundError`).
- `infrastructure/config.py` — added `_DELEGATION_SECTION_COMMENT` module-level constant (follows `_GLOBAL_YAML_HEADER` / `_SECRETS_YAML_HEADER` pattern). `_render_default_global_yaml()` now appends this constant as a string suffix after `yaml.safe_dump` (commented-out YAML cannot be emitted by safe_dump — string suffix is the only correct approach). DelegationConfig / AgentDelegationConfig / GlobalConfig / AgentConfig / loaders were NOT touched (frozen from batch 1).
- `config/global.example.yaml` — added commented `delegation:` section with `max_iterations_per_sub: 10`, `timeout_seconds: 60`, explanatory comment noting per-agent `enabled: true` + `allowed_targets: [...]` still required, and explicit NOTE that there is no `max_depth` field (recursion prevention is by construction).
- `config/agents/coordinator.example.yaml` — new file (and new `config/agents/` directory). Minimal coordinator example with `delegation.enabled: true`, `allowed_targets: [researcher, coder]` placeholders, minimum required `AgentConfig` fields (id, name, description, system_prompt, llm override). Other subconfigs (embedding, memory, chat_history, tools, workspace) intentionally omitted — inherit from `global.yaml` via 4-layer merge. `AgentRegistry` auto-skips files matching `.example` in name (guard at `config.py:456`).

### Batch 6 (tasks 6.1, 6.2)
- `infrastructure/container.py` — `wire_delegation` post-wiring block added: calls new private `_build_discovery_section(get_agent_container)` method which resolves each `allowed_targets` entry via the closure (EAGER at wire time, not lazy), collects `(id, description, tool_names)` for each resolvable target, and formats a markdown discovery section. When the section is non-empty, calls `self.run_agent.set_extra_system_sections([discovery_section])`. Unknown targets are SILENTLY skipped — no raise, no log-error. Empty allow-list / all-unknown cases → no call to the setter, `run_agent._extra_system_sections` stays empty.
- `core/use_cases/run_agent.py` — `RunAgentUseCase.__init__` now stores `self._extra_system_sections: list[str] = []`. New setter `set_extra_system_sections(sections: list[str]) -> None`. In `execute()`, the call site of `AgentContext.build_system_prompt` now passes `extra_sections=self._extra_system_sections or None`. Empty-list-to-None coalesce preserves the batch-1 non-breaking default. `RunAgentOneShotUseCase` was NOT touched — parent-only by construction.
- `adapters/outbound/tools/delegate_tool.py` — new module-level constant `_RESULT_FORMAT_FOOTER`. At the hook-point in `execute()` (after registry lookup, before `child_one_shot.execute` call), builds `effective_system_prompt`: base = caller's `system_prompt` if not None else `target_container.agent_config.system_prompt`, then appends `_RESULT_FORMAT_FOOTER`. Wrapped in try/except to preserve the never-raises guarantee — on attribute access failure, falls back to footer-alone. Passed to `child_one_shot.execute(system_prompt=effective_system_prompt, ...)`.

### Batch 7 (tasks 7.1, 7.2, 7.3 + PRODUCTION BUG FIX)
- `tests/unit/use_cases/test_delegation_integration.py` — **new** file; 15 integration tests (1 happy path + 9 failure modes + 5 REQ-DG-9) wiring REAL `AgentContainer` instances with a mocked LLM port and real `ToolRegistry`.
- `core/use_cases/run_agent_one_shot.py` **[PRODUCTION BUG FIX]** — one-line schema filter fix at ~line 86:
  - Before: `tool_schemas = [s for s in all_schemas if s.get("name") != _DELEGATE_TOOL_NAME]`
  - After: `tool_schemas = [s for s in all_schemas if s.get("function", {}).get("name") != _DELEGATE_TOOL_NAME]`
  - **Root cause**: `ToolRegistry.get_schemas()` returns nested OpenAI-format schemas `{"type": "function", "function": {"name": ...}}`. The batch-3 filter checked the top-level `"name"` key which is `None` in the nested format → filter was a NO-OP. Real `delegate` tool was never being filtered from child schemas. **REQ-DG-9 (recursion prevention by construction) was BROKEN in production** but passed unit tests because batch-3 tests mocked schemas with the wrong (flat) format. Discovered by integration tests when they wired a real `ToolRegistry`.
- `tests/unit/use_cases/test_run_agent_one_shot.py` — 5 batch-3 tests updated to use nested schema format `{"type": "function", "function": {"name": ..., "description": ...}}`. All REQ-DG-9 and REQ-OS-4 tests now exercise the real schema shape, not the flat mock shape. All 17 pass.

### Batch 8 (latent bug fix — decouple one-shot from wire_delegation)
- `infrastructure/container.py` — `AgentContainer.__init__` now constructs `self.run_agent_one_shot = RunAgentOneShotUseCase(llm, tools, agent_config)` UNCONDITIONALLY (every agent gets a one-shot use case regardless of delegation config). The same construction was REMOVED from `wire_delegation` — that method now only handles delegation-specific concerns: `DelegateTool` registration + `_build_discovery_section` + `set_extra_system_sections`. The `enabled=False` no-op early return still exists and still preserves REQ-DG-1. Idempotency flag `_delegation_wired` unchanged.
- `tests/unit/infrastructure/test_container.py` — `_build_minimal_container` helper now injects `run_agent_one_shot`. `test_wire_delegation_noop_when_disabled` updated: old invariant asserted `run_agent_one_shot` absent when disabled; new invariant asserts it IS present (from `__init__`) AND `delegate` tool is NOT registered. `test_app_container_two_phase_init` similarly updated. 3 new tests added (15-17): unconditional one-shot in `__init__` regardless of delegation config, one-shot present BEFORE `wire_delegation` is called, identity preservation (`wire_delegation` must NOT re-assign `run_agent_one_shot`).
- `tests/unit/use_cases/test_delegation_integration.py` — `_build_container` helper updated to inject `run_agent_one_shot` (uses `__new__` + manual injection, so it bypasses `__init__` and needed explicit update — structural alignment with the new invariant). Added `test_child_with_delegation_disabled_can_be_delegation_target`: parent delegates to a worker with `delegation.enabled=False`, verifies the parent's `execute()` returns without `AttributeError`, the `DelegationResult` has `status="success"`, the worker has `run_agent_one_shot` set, and the worker's tool registry does NOT contain `delegate`.

## Tests added/modified

### Batch 1
- `tests/unit/domain/test_errors.py` — new file; 6 tests
- `tests/unit/domain/test_delegation_result.py` — new file; 12 tests
- `tests/unit/domain/test_agent_context.py` — extended; 7 new tests
- `tests/unit/infrastructure/test_config.py` — extended; 17 new tests

### Batch 2
- `tests/unit/use_cases/test_tool_loop.py` — new file; 13 tests
- `tests/unit/use_cases/test_result_parser.py` — new file; 19 tests

### Batch 3
- `tests/unit/use_cases/test_run_agent_one_shot.py` — new file; 17 tests covering REQ-OS-1..4 + REQ-DG-9 (no-history, prompt-override, timeout+max-iter propagation, full schemas without RAG, delegate-tool filtering)
- No new tests for 1.3 — the existing 14 `test_run_agent_basic.py` tests remain the acceptance bar (all green)

### Batch 4
- `tests/unit/adapters/tools/test_delegate_tool.py` — new file; 24 tests covering all canonical reason strings (REQ-DG-2 `target_not_allowed`, REQ-DG-3 `unknown_agent`, REQ-DG-4 happy path, REQ-DG-5 `result_parse_error` no-block + invalid-JSON, REQ-DG-6 `timeout` + `max_iterations_exceeded`, REQ-DG-8 `child_exception:<Type>` + never-raises guarantee across all failure modes, canonical-strings exactness, JSON round-trip of `ToolResult.output`)

### Batch 5
- `tests/unit/infrastructure/test_container.py` — new file; 5 tests: REQ-DG-1 enabled=False no-op + no `run_agent_one_shot` attr + no `delegate` tool registered, REQ-DG-1 enabled=True → instance assigned + tool registered with correct config, idempotency (double call = same instance + single tool registration), AppContainer two-phase init with 3 agents (A enabled, B+C disabled), late-binding closure check (closure resolves against app.agents dict, not a snapshot).
- `tests/unit/infrastructure/test_config.py` — extended; 3 new tests for `_render_default_global_yaml()` asserting the rendered output contains `delegation`, `max_iterations_per_sub`, `timeout_seconds` tokens.

### Batch 6
- `tests/unit/infrastructure/test_container.py` — 2 batch-5 tests updated (`test_wire_delegation_registers_tool_when_enabled` now asserts `get_container_mock.assert_called_once_with("specialist-agent")` because `_build_discovery_section` calls the closure eagerly during wire; `_build_minimal_container` helper now injects a `run_agent` mock since 6.1 needs a setter target). 9 new tests: discovery section present when enabled, filtered by allow-list, absent when disabled, empty allow-list skips section, unknown targets skipped, all targets unknown skips section, mixed targets (partial filter), one-shot isolation (no `_extra_system_sections` attribute on `RunAgentOneShotUseCase`), idempotency preserved with discovery.
- `tests/unit/use_cases/test_run_agent_basic.py` — 3 new tests: thread-through (setting `_extra_system_sections=["SECTION-TEST"]` causes the prompt passed to the LLM to contain `"SECTION-TEST"`), empty default (`_extra_system_sections` is `[]` on fresh construction), setter replaces existing (calling setter twice replaces the list, does not append).
- `tests/unit/adapters/tools/test_delegate_tool.py` — 2 batch-4 tests updated (pass-through assertions replaced to reflect footer-always-appended contract). 6 new tests: footer appended when `system_prompt=None` (base is child default), footer appended when caller provides `system_prompt` (base is override), footer literal substring audit-check, footer never missing (parametrized over None / non-None), happy-path regression still works with footer, never-raises holds when `agent_config` lookup fails (fallback to footer-alone, returns a `child_exception:*` result).

## Test status

### Batch 1
- `pytest tests/unit/domain/test_errors.py -q` → 6 passed
- `pytest tests/unit/domain/test_delegation_result.py -q` → 12 passed
- `pytest tests/unit/domain/test_agent_context.py -q` → 14 passed
- `pytest tests/unit/infrastructure/test_config.py -q` → 36 passed
- Full unit suite: 193 passed, 5 pre-existing failures in `test_schedule_task.py` (unrelated)

### Batch 2
- `pytest tests/unit/use_cases/test_tool_loop.py -q` → 13 passed
- `pytest tests/unit/use_cases/test_result_parser.py -q` → 19 passed

### Batch 3
- `pytest tests/unit/use_cases/test_run_agent_basic.py -q` → 14 passed (acceptance criterion for 1.3)
- `pytest tests/unit/use_cases/test_run_agent_one_shot.py -q` → 17 passed

### Batch 4
- `pytest tests/unit/adapters/tools/test_delegate_tool.py -q` → 24 passed

### Batch 5
- `pytest tests/unit/infrastructure/test_container.py -q` → 5 passed
- `pytest tests/unit/adapters/tools/test_delegate_tool.py -q` → 24 passed (regression check after 5.1 wiring — zero regressions)
- `pytest tests/unit/infrastructure/test_config.py -q` → 39 passed (36 pre-existing + 3 new render tests)

### Batch 6
- `pytest tests/unit/infrastructure/test_container.py -q` → 14 passed (5 original, 2 updated, 9 new for 6.1)
- `pytest tests/unit/use_cases/test_run_agent_basic.py -q` → 17 passed (14 original + 3 new for 6.1)
- `pytest tests/unit/adapters/tools/test_delegate_tool.py -q` → 30 passed (24 original, 2 updated, 6 new for 6.2)
- `pytest tests/unit/use_cases/test_run_agent_one_shot.py -q` → 17 passed (regression check — one-shot isolation confirmed unchanged)

### Batch 7 (full regression sweep of all change-touched files)
- `pytest tests/unit/use_cases/test_delegation_integration.py -q` → **15 passed** (new file)
- `pytest tests/unit/adapters/tools/test_delegate_tool.py -q` → **30 passed**
- `pytest tests/unit/infrastructure/test_container.py -q` → **14 passed**
- `pytest tests/unit/use_cases/test_run_agent_basic.py -q` → **17 passed**
- `pytest tests/unit/use_cases/test_run_agent_one_shot.py -q` → **17 passed** (5 tests updated for nested schema format)

### Batch 8 (post-fix regression sweep)
- `pytest tests/unit/infrastructure/test_container.py -q` → **17 passed** (14 original + 3 new for decouple)
- `pytest tests/unit/use_cases/test_delegation_integration.py -q` → **16 passed** (15 existing + 1 new for disabled-child-as-target)
- `pytest tests/unit/adapters/tools/test_delegate_tool.py -q` → **30 passed** (no regression)
- `pytest tests/unit/use_cases/test_run_agent_basic.py -q` → **17 passed** (no regression)
- `pytest tests/unit/use_cases/test_run_agent_one_shot.py -q` → **17 passed** (no regression)

**Change-total across all 10 test files: 199 passed, 0 failed.**

## Deviations from design

### Batch 1
None.

### Batch 2
- `_extract_tool_calls` and `run_tool_loop` are module-level functions in `_tool_loop.py` (underscore-private helper, public loop). Matches design.
- `parse_delegation_result` returns a `DelegationResult` with a `summary` field populated on error paths because `summary` is required by the pydantic model. Implementation detail, no spec conflict.

### Batch 3
- **Design vs tasks conflict on `asyncio.wait_for` ownership — RESOLVED in favor of tasks spec.** The design doc Risks section said "`DelegateTool` owns the `asyncio.wait_for` wrapper." The tasks spec (task 2.4 description + REQ-OS-3 test requirement) says `RunAgentOneShotUseCase` wraps `run_tool_loop` in `asyncio.wait_for`. Implemented per tasks spec because it is more specific and directly drives REQ-OS-3 test coverage. **Consequence for task 3.2**: `DelegateTool` must catch `asyncio.TimeoutError` (propagated from the use case) and map it to `DelegationResult(reason="timeout")` — it must NOT wrap its own `wait_for`.
- **1.3 cleanup note**: the docstring said "BOTH call sites" but `RunAgentUseCase` has only ONE call site to `_run_with_tools` (inside `execute()`); `inspect()` does not use the tool loop. Only one rewire was needed.

### Batch 4
- None. The batch-3 deviation (`RunAgentOneShotUseCase` owns `wait_for`) was honored exactly. All canonical reason strings matched the design table.
- **Attribute name decision (load-bearing for 5.1)**: `DelegateTool` reads `container.run_agent_one_shot.execute(...)`. Task 5.1's `wire_delegation` MUST assign the use case instance to `self.run_agent_one_shot` (attribute name `run_agent_one_shot`, consistent with existing `run_agent` naming). NOT `one_shot_use_case`, NOT `run_one_shot`.

### Batch 5
- **5.1**: Design sketch showed `RunAgentOneShotUseCase(embedder=..., skills=..., ...)` but the actual constructor shipped in batch 2 is `(llm, tools, agent_config)` — three positional args. Implementation honors the real constructor, not the stale design sketch. No spec conflict — the spec just says "one-shot use case", not the constructor shape.
- **5.1**: `AppContainer.get_agent` raises `AgentNotFoundError` on miss. The Phase 2 `get_agent_container` closure uses `self.agents.get(agent_id)` instead (returns `None`) to match `DelegateTool`'s expected `Callable[[str], AgentContainer | None]` contract. This is the correct contract for `unknown_agent` handling.
- **8.1**: `_DELEGATION_SECTION_COMMENT` is a module-level constant string appended as a suffix to the `yaml.safe_dump` output (rather than merged into the defaults dict). Justification: `safe_dump` cannot emit commented-out YAML, so a string suffix is the only correct approach. Follows the existing `_GLOBAL_YAML_HEADER` / `_SECRETS_YAML_HEADER` pattern.
- **8.2**: None. The coordinator example omits `embedding`, `memory`, `chat_history`, `tools`, `workspace` subconfigs intentionally — they inherit via the 4-layer YAML merge from `global.yaml`. `AgentRegistry` auto-skips `.example` files at `config.py:456`, so no runtime loading risk.

### Batch 6
- **6.1 eager closure call**: Batch 5's `wire_delegation` did NOT call the `get_agent_container` closure during wiring — it only stashed it for `DelegateTool.execute` to use lazily at call time. Batch 6 adds `_build_discovery_section` which CALLS the closure during wire to resolve target descriptions/tool names. This changed the contract: 2 batch-5 tests asserting `get_container_mock.assert_not_called()` were updated to `assert_called_once_with(...)`. The eager-call is intentional — the discovery section is built once at startup and frozen in the parent's system prompt. Hot-reload of child descriptions would require re-wiring. Noted for future hot-reload work.
- **6.1 empty-list-to-None coalesce**: In `RunAgentUseCase.execute()`, `extra_sections=self._extra_system_sections or None`. This preserves the batch-1 `build_system_prompt` contract where `extra_sections=None` means "no change from base prompt." Using `[]` directly would be a different code path — coalescing to `None` keeps the happy path unchanged.
- **6.2 footer wrap in try/except**: The effective-prompt construction reads `target_container.agent_config.system_prompt` which could AttributeError if the agent_config shape differs from expectations. The try/except falls back to footer-alone rather than propagating — this is required by REQ-DG-8 (never-raises). The fallback means the child receives only the footer as its system prompt, which is minimal but still parseable.
- **6.2 test helper change**: `_make_child_container` gained a `default_system_prompt` parameter with a sensible default. Batch 4 tests did not exercise the default-prompt path, so they were updated to pass the new parameter explicitly. The 2 updated tests (`test_passes_correct_args_to_child_one_shot`, renamed `test_passes_none_system_prompt_when_not_provided` → `test_passes_child_default_plus_footer_when_no_system_prompt_provided`) document the new post-6.2 contract.

### Batch 7 — CRITICAL PRODUCTION BUG FIXED
- **REQ-DG-9 schema filter was a NO-OP in production until batch 7.** The batch-3 filter in `run_agent_one_shot.py` was `s.get("name") != _DELEGATE_TOOL_NAME`, but `ToolRegistry.get_schemas()` returns nested OpenAI-format schemas `{"type": "function", "function": {"name": ...}}`. The top-level `"name"` key is `None`, so the filter never excluded anything — every non-None comparison passed, `delegate` was NEVER actually filtered. The batch-3 unit tests passed because they mocked schemas with a flat `{"name": ..., "description": ...}` format that did not match the real registry output. This means REQ-DG-9 (recursion prevention by construction) was vacuously "tested" but never actually validated against real production data until integration tests wired a real `ToolRegistry`.
  - **Fix applied**: `s.get("function", {}).get("name") != _DELEGATE_TOOL_NAME`.
  - **Tests updated**: 5 tests in `test_run_agent_one_shot.py` changed from flat to nested schema fixtures. REQ-DG-9 and REQ-OS-4 now exercise the real shape.
  - **Consequence**: This is a bug fix, not a design change. REQ-DG-9 was always the intent; the implementation just had a wrong key path. Integration test 7.3 (dedicated) + overlap in 7.1 now BOTH validate this against real schemas.

### Batch 8 — LATENT BUG FIXED (decouple one-shot from delegation wiring)
- **The disabled-child-as-target bug is fixed.** `RunAgentOneShotUseCase` is now constructed in `AgentContainer.__init__` UNCONDITIONALLY. Any agent — regardless of `delegation.enabled` — can be a delegation target. `wire_delegation` is now strictly about delegation concerns (tool registration + discovery section), not about building one-shot use cases.
- **REQ-DG-1 preserved**: `delegate` tool still absent from a container's tool registry when `delegation.enabled=False`. The fix is orthogonal to REQ-DG-1.
- **Test-helper ripple**: both `_build_minimal_container` (test_container.py) and `_build_container` (test_delegation_integration.py) use `__new__` + manual attribute injection instead of calling `__init__`. Both had to be updated to inject `run_agent_one_shot` after the decoupling. This is NOT scope creep — it is structural alignment with the new invariant. Any future test helper that builds containers via `__new__` must also set this attribute.
- **New explicit invariants** (guard against future regression):
  1. `AgentContainer.run_agent_one_shot` is set in `__init__` for every agent, regardless of `delegation.enabled`.
  2. `wire_delegation` never constructs or re-assigns `run_agent_one_shot` — only the delegate tool + discovery section.
  3. Identity preservation: `wire_delegation` leaves `run_agent_one_shot` untouched as a Python object (same `id()`).
  4. REQ-DG-1 (delegate tool absent when disabled) is preserved.

## Next recommended step

All 17 implementation tasks are done + 2 bug fixes landed in batches 7 and 8. The change is READY for `/sdd-verify`.

**Summary of bugs caught and fixed during batches 7-8:**
1. REQ-DG-9 schema filter was a no-op in production (batch 7, 1-line fix + 5 test updates)
2. Disabled children couldn't be delegation targets (batch 8, decoupling fix + 3 new tests + 2 helper updates + 1 new integration test)

Both bugs are now covered by tests that use REAL schemas / REAL invariants, not stale mock shapes.

Run `/sdd-verify agent-delegation` to validate against spec. After verify passes → `/sdd-archive agent-delegation`.

## Key risks for next batches (7.x integration tests)

- **Closure contract**: `get_agent_container` closure uses `self.agents.get(agent_id)` returning `None` on miss (NOT `AppContainer.get_agent` which raises). Integration tests exercising `unknown_agent` should rely on this behavior.
- **Canonical reason strings (exact match required)**: `target_not_allowed`, `unknown_agent`, `result_parse_error`, `timeout`, `max_iterations_exceeded`, `child_exception:<Type>`. NO `malformed_result`, NO bare `max_iterations`, NO `max_depth_exceeded`, NO `unknown_tool`. Integration tests must assert the exact strings.
- **Load-bearing `container.run_agent_one_shot`** attribute name — already wired in 5.1, DelegateTool reads this exact name.
- **Load-bearing silent-fallthrough catch** in `RunAgentUseCase.execute()` — must not be disturbed by integration test mocks.
- **Footer always appended (post-6.2)**: `DelegateTool.execute` ALWAYS appends `_RESULT_FORMAT_FOOTER` to the child's system prompt (whether caller provided override or not). Integration tests mocking the child LLM must produce a valid trailing ```json``` block in the happy-path mock response.
- **Discovery section eager at wire**: `_build_discovery_section` calls `get_agent_container` during wire, not lazily. Integration tests that check the parent's system prompt must ensure `wire_delegation` ran after all target containers were built.
- **One-shot isolation**: `RunAgentOneShotUseCase` never receives `extra_system_sections`. Child prompts contain the footer (from 6.2) but NOT the agent-discovery section (from 6.1). This is by design — children should not see who else exists.
- **REQ-DG-9 recursion filter**: `RunAgentOneShotUseCase` filters `"delegate"` from schemas BEFORE calling `run_tool_loop`. Test 7.3 must verify this at integration level — the child's LLM mock should never be called with a schemas list containing `delegate`.
- **Empty schemas case**: child with zero tools besides `delegate` → filtered schemas list is empty → `run_tool_loop` handles this.
