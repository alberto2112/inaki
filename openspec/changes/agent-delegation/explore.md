# Exploration: agent-delegation

## Problem statement

Inaki's multi-agent system today runs agents as completely isolated units — each with its own REST port, Telegram bot, workspace, tool registry, and conversation history. There is no mechanism for one agent (the "parent") to delegate a subtask to another agent (the "child") and receive a structured result back. This limits composition: a coordinator agent can't leverage a specialist agent's capability surface without duplicating all its tools and prompt.

The goal is to add **agent-to-agent delegation** via a first-class `delegate` tool call. The child runs one-shot (no history load/persist), the parent controls framing (system_prompt, tools), depth is bounded, and the child returns a machine-parseable JSON result block.

---

## Current state (validated)

### Agent loop — `core/use_cases/run_agent.py`
- **Validated.** `execute()` is at lines 85–115 exactly as described.
- Flow: load history (88) → read digest (89) → list all skills/tools (90-91) → RAG embed if threshold exceeded (97-102) → build `AgentContext` + system_prompt (104-105) → `_run_with_tools` loop (110) → persist user+assistant (112-113).
- `_run_with_tools` is at lines 152-226: standard tool-call loop with circuit breaker (threshold from `cfg.tools.circuit_breaker_threshold`), max iterations from `cfg.tools.tool_call_max_iterations`.
- `inspect()` at 117-150 runs the full RAG pipeline without calling the LLM and without persisting — this is the closest existing "dry-run" path.

### Tool dispatch — `adapters/outbound/tools/tool_registry.py`
- **Validated.** `ToolRegistry` at lines 26-109.
- `get_schemas_relevant(query_embedding, top_k)` does cosine similarity over pre-embedded tool descriptions (lines 81-108). **No explicit-list bypass exists.** Only RAG selection or `get_schemas()` (all tools).
- `execute()` returns `ToolResult(success=False)` for unknown tools — never raises.

### Per-agent DI — `infrastructure/container.py`
- **Validated.** `AgentContainer` at lines 47-191, `AppContainer` at 193-345.
- `AppContainer.get_agent(agent_id: str) -> AgentContainer` at lines 339-345 — this is the exact seam for resolving sibling agents.
- `AppContainer.agents: dict[str, AgentContainer]` holds all loaded agents.

### Config — `infrastructure/config.py`
- **Validated.** `AgentConfig` at lines 165-178 — pure Pydantic `BaseModel`. No `delegation` section exists today.
- `GlobalConfig` at lines 184-193 — same, no `delegation` section.
- Config loading uses `_deep_merge` + Pydantic construction at call sites — adding a new Pydantic sub-model (`DelegationConfig`) and referencing it from `AgentConfig`/`GlobalConfig` is clean and forwards-compatible (unknown keys are silently ignored by `_deep_merge` + Pydantic strict=False default).
- `AgentRegistry` at lines 421-456 provides `list_all() -> list[AgentConfig]` — source of truth for agent discovery.

### `AgentContext.build_system_prompt` — `core/domain/value_objects/agent_context.py`
- **Validated.** Lines 10-25. Appends sections via list join. Sections: base_prompt → memory_digest (if present) → skills block (if present).
- **Critical finding**: `build_system_prompt` accepts `base_prompt: str` and builds on top of it. It does NOT accept injected extra sections as parameters. To add an "available agents" section or a "result format footer", either: (a) modify `AgentContext` to accept `extra_sections: list[str]`, or (b) pre-process `base_prompt` before passing it, or (c) wrap the system_prompt string after calling `build_system_prompt`.

### Tests — `tests/unit/use_cases/test_run_agent_basic.py`
- **Validated.** 13 tests covering: LLM response passthrough, history persist, history load, RAG embed_query calls, memory.search not called, digest injection, digest-absent handling, OSError/UnicodeDecodeError resilience, inspect result shape.
- All tests depend on `RunAgentUseCase.__init__` signature and the `execute(user_input: str) -> str` contract. A new `RunAgentOneShotUseCase` class will NOT break these tests.
- Tests use `AsyncMock` / `MagicMock` — no integration with real containers.

### No delegation today
- Confirmed zero matches for `delegate`, `sub_agent`, `handoff`, `spawn`, `one_shot` in functional code.
- `consolidate_all_agents.py` is the only multi-agent iteration pattern — sequential, not delegating.

---

## Design decisions (pre-made, confirmed)

All decisions from the architectural conversation are confirmed compatible with the codebase:

1. **Child context is clean** — skip `history.load` and `history.append`. The `execute()` method does both; a new `run_one_shot` method/class can skip them without touching the existing path.
2. **Parent controls system_prompt** — `build_system_prompt` can be extended or bypassed cleanly.
3. **Recursive delegation with `max_depth`** (default 2) via `asyncio.ContextVar` — no asyncio issues found; the project is async-first throughout.
4. **Structured result contract** — trailing JSON block in child's final LLM response.
5. **Opt-in per agent** via YAML `delegation.enabled: true` — Pydantic sub-model, defaults to disabled.
6. **Parent discovers available agents** via injected system_prompt section from `AgentRegistry`.
7. **Config additions**: `DelegationConfig` sub-model in `AgentConfig` and `GlobalConfig`.

---

## Approaches considered

### 1. One-shot execution: new use case vs flag on existing `RunAgentUseCase`

| Approach | Pros | Cons | Complexity |
|----------|------|------|------------|
| **A. New `RunAgentOneShotUseCase`** | Clean separation; existing tests untouched; SRP; easy to test independently | Slight code duplication of `_run_with_tools` and RAG pipeline setup | Low-Medium |
| **B. Flag on existing (`one_shot=False` param)** | Single class, less duplication | Pollutes the execute() signature; conditional logic in hot path; risks breaking existing tests; harder to reason about | Low-Medium |
| **C. Shared base class / mixin** | DRY for `_run_with_tools` | Overengineering for current scale; Python MRO complexity | Medium-High |

**Recommendation: A.** New use case `core/use_cases/run_agent_one_shot.py`. It accepts the same dependencies but `execute()` skips `history.load` and `history.append`, passes no history to `_run_with_tools` (just `[user_msg]`), and omits the memory digest. `_run_with_tools` can be extracted to a shared module or duplicated — at this scale, duplication is acceptable. The key insight: `RunAgentUseCase._run_with_tools` is already a self-contained private method — it can be extracted to `core/use_cases/_tool_loop.py` and imported by both.

### 2. Depth tracking: ContextVar vs explicit param

| Approach | Pros | Cons | Complexity |
|----------|------|------|------------|
| **A. `asyncio.ContextVar`** | Propagates automatically through async call chain; zero API surface pollution; clean parent→child propagation | Slightly implicit; requires discipline not to reset it in wrong places | Low |
| **B. Explicit `depth: int` param on `execute()`** | Explicit, visible in call stack | Changes `execute()` signature; pollutes `RunAgentUseCase` interface; callers (REST/Telegram) need to pass 0 | Low-Medium |
| **C. Thread-local / global state** | N/A — async project, can't use threading.local reliably | Breaks with concurrent delegations | High risk |

**Recommendation: A.** `ContextVar` is the right primitive for this pattern in async Python. Declare `_delegation_depth: ContextVar[int] = ContextVar('delegation_depth', default=0)` in `delegate_tool.py`. The context copy propagates to child coroutines automatically via asyncio task inheritance.

### 3. Result parsing: trailing JSON block vs structured tool output

| Approach | Pros | Cons | Complexity |
|----------|------|------|------------|
| **A. Trailing JSON block in LLM response** (decided) | Works with any LLM that can follow instructions; parent receives human-readable + machine text | Parsing fragility; LLM may not always comply; need fallback | Low |
| **B. Structured tool output — child calls a `report_result` tool** | Guaranteed structure if LLM calls the tool | Child might loop waiting for the tool; adds a tool to every child | Medium |
| **C. Response format enforcement (JSON mode)** | Guaranteed JSON if provider supports it | Not all providers support it; loses human-readable summary | Provider-dependent |

**Recommendation: A** (confirmed). Parser extracts last ```json ... ``` block from response. Fallback: `status: "failed"`, raw text in `details`. This matches the decided contract and is the most LLM-agnostic approach. A `core/utils/delegation_result_parser.py` utility handles extraction + fallback cleanly.

### 4. Agent discovery injection: static vs dynamic

| Approach | Pros | Cons | Complexity |
|----------|------|------|------------|
| **A. Dynamic — generated at delegation time from `AgentRegistry`** (decided) | Always current; reflects actual loaded agents and their tools | Slightly more work per call | Low |
| **B. Static — written into parent's system_prompt YAML** | Zero runtime cost | Stale when agents change; manual maintenance | Low but brittle |
| **C. Dynamic — cached at startup** | Fast; avoids per-call regeneration | Cache invalidation when agents reload | Low-Medium |

**Recommendation: A** (confirmed). Generate the available-agents section once per parent's `execute()` call, not per delegation. The section is injected into the parent's system_prompt before the tool loop via an extra `extra_sections` parameter on `AgentContext.build_system_prompt` — a minimal, non-breaking extension.

---

## Recommended direction

1. **Extract `_run_with_tools`** to `core/use_cases/_tool_loop.py` (shared private module). Both `RunAgentUseCase` and `RunAgentOneShotUseCase` import it.
2. **New use case** `core/use_cases/run_agent_one_shot.py` — same deps, no history, no digest, optional `system_prompt` override and `tool_schemas` override.
3. **New tool** `adapters/outbound/tools/delegate_tool.py` — implements `ITool`, receives `get_agent_container: Callable[[str], AgentContainer]` at construction, enforces `max_depth` via `ContextVar`, validates `allowed_targets`.
4. **Config extension**: `DelegationConfig` Pydantic model added to `infrastructure/config.py`; added as optional field to `AgentConfig` and `GlobalConfig` with safe default (disabled).
5. **`AgentContext` extension**: add `extra_sections: list[str] = []` to `build_system_prompt` signature — appends them after skills. Non-breaking.
6. **Result parser utility** `core/utils/delegation_result_parser.py` — regex extracts last ```json``` block, validates schema, returns `DelegationResult` dataclass.
7. **`ToolRegistry` extension**: add `get_schemas_by_names(names: list[str]) -> list[dict]` method for explicit tool selection bypass. One-liner filter over `self._tools`.
8. **Register `DelegateTool` in `AgentContainer._register_tools`** only when `agent_config.delegation.enabled == True`, injecting `app_container.get_agent` as the resolver.

---

## Risks & open questions

1. **Circular dependency risk**: `delegate_tool.py` needs to call `AgentContainer.run_agent_one_shot.execute()`, but `AgentContainer` is defined in `infrastructure/container.py` which imports from `adapters/outbound/tools/`. The `Callable[[str], AgentContainer]` injection pattern avoids the import cycle at module level — confirmed safe, but requires care in `container.py` to register the tool after all containers are built (i.e., in `AppContainer.__init__` after the agent loop, not inside `AgentContainer._register_tools`). This means delegation tool registration must move out of `AgentContainer` into `AppContainer`, OR use a lazy resolver pattern.

2. **`AgentContainer._register_tools` is called in `AgentContainer.__init__`** — at that point, `AppContainer` doesn't exist yet. The `get_agent` callable must be injected later (post-construction wiring) or via `AppContainer` after all `AgentContainer`s are built. Recommended: add a `wire_delegation(get_agent: Callable)` method to `AgentContainer`, called from `AppContainer.__init__` after the agent loop.

3. **Result format compliance**: The structured JSON block in the child's final response depends on the LLM following instructions. If the LLM produces partial or no JSON, the parser falls back to `status: "failed"`. Parent agents must handle this gracefully. The result-format footer injected into the child's system_prompt (via `sp_append`) needs to be concise and tested across LLM providers.

4. **Test coverage gap**: `test_run_agent_basic.py` has 13 tests for `RunAgentUseCase`. The new `RunAgentOneShotUseCase` and `DelegateTool` will need parallel test coverage. No existing test would break from these additions — confirmed by reviewing test fixtures (all use `AsyncMock` on interfaces).

5. **`ToolsConfig.tool_call_max_iterations`** applies to the child loop too. `max_iterations_per_sub` in the delegation config will override this for child executions — needs explicit pass-through when constructing the one-shot use case or a dedicated config field.

---

## Relevant files (with line numbers)

- `core/use_cases/run_agent.py:85–115` — `execute()` — history load + persist, RAG, tool loop entry point
- `core/use_cases/run_agent.py:152–226` — `_run_with_tools()` — tool loop + circuit breaker (candidate for extraction)
- `core/domain/value_objects/agent_context.py:10–25` — `build_system_prompt()` — needs `extra_sections` param
- `adapters/outbound/tools/tool_registry.py:81–108` — `get_schemas_relevant()` — needs `get_schemas_by_names()` sibling
- `infrastructure/container.py:47–111` — `AgentContainer` — `_register_tools()` wiring point
- `infrastructure/container.py:193–209` — `AppContainer.__init__` — agent loop, post-construction wiring point for delegation
- `infrastructure/container.py:339–345` — `AppContainer.get_agent()` — sibling resolver seam
- `infrastructure/config.py:165–178` — `AgentConfig` — add `delegation: DelegationConfig`
- `infrastructure/config.py:184–193` — `GlobalConfig` — add `delegation: DelegationConfig`
- `core/domain/errors.py` — add `DelegationError`, `DelegationDepthExceededError`, `DelegationTargetNotAllowedError`
- `tests/unit/use_cases/test_run_agent_basic.py` — existing test suite (will NOT break with new use case)

---

## Ready for Proposal

Yes. All pre-made design decisions are validated against the codebase. The key discovery not in the prior conversation is the **post-construction wiring problem** for `DelegateTool` registration (Risk #1/#2 above) — this needs an explicit solution in the proposal. Everything else is confirmed compatible.
