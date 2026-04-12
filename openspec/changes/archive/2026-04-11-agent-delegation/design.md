# Design: agent-delegation

## Scope

This document answers the specific engineering questions that surfaced while writing the spec. For intent, requirements, scenarios, and failure-mode rationale, see `proposal.md` and `spec.md` in this same folder. This doc is deliberately narrow: recursion prevention strategy, container wiring, tool delivery to one-shot, the tool-loop boundary, and the canonical reason-string table.

## Q1 — Recursion prevention

Recursive delegation is prevented by construction, not by runtime enforcement. `RunAgentOneShot` excludes `DelegateTool` from the tool schemas it passes to the child LLM. The child literally has no `delegate` tool available — it cannot call what does not exist in its schema.

This eliminates the need for:
- A `ContextVar` for depth tracking
- A `max_depth` config option
- A `max_depth_exceeded` failure mode
- Concerns about `ContextVar` propagation with `asyncio.create_task` or `asyncio.gather`

**Implementation**: `RunAgentOneShot.execute` receives the full `ToolRegistry` from its owning `AgentContainer`, but when building the schemas list it filters out any tool whose name is `"delegate"`. One line of code, zero runtime overhead, impossible-by-construction guarantee.

## Q2: Post-construction wiring in AppContainer

**Current state** (`infrastructure/container.py:201-208`): `AppContainer.__init__` builds each `AgentContainer` in a single pass. Each container registers its tools inline in `AgentContainer._register_tools` — BEFORE the sibling containers exist.

**Problem**: `DelegateTool` needs `get_agent_container: Callable[[str], AgentContainer]` that can resolve any sibling. This only works post-loop.

**Solution — two-phase wiring**:

1. Phase 1: `AppContainer.__init__` constructs all `AgentContainer`s (existing loop, unchanged).
2. Phase 2: `AppContainer.__init__` iterates again. For each container whose `agent_config.delegation.enabled` is `True`, calls `container.wire_delegation(get_agent_container=self.get_agent)`.

`AgentContainer.wire_delegation`:
```python
def wire_delegation(self, get_agent_container: Callable[[str], "AgentContainer"]) -> None:
    if not self.agent_config.delegation.enabled:
        return  # no-op safety
    run_one_shot = RunAgentOneShotUseCase(
        llm=self._llm,
        embedder=self._embedder,
        skills=self._skills,
        tools=self._tools,
        agent_config=self.agent_config,
    )
    delegate_tool = DelegateTool(
        parent_config=self.agent_config,
        global_delegation_config=self._global_cfg.delegation,
        agent_registry=self._registry,  # stored in __init__
        get_agent_container=get_agent_container,
    )
    self._tools.register(delegate_tool)
```

**Key decisions**:
- `wire_delegation` is a no-op when `delegation.enabled: false`. `DelegateTool` is never registered, so it never appears in `get_schemas()` — REQ-DG-1 satisfied by construction.
- `get_agent_container` is a bound method `self.get_agent` of `AppContainer`. Python closes over `self`; no circular reference problem at module-import time because wiring runs at instance-construction time.
- `AgentContainer.__init__` must stash `global_config` and `registry` as attributes so `wire_delegation` can construct `DelegateTool` and `RunAgentOneShotUseCase` without re-plumbing them.

**Exact sequence in `AppContainer.__init__`**:
1. Build `self.agents` dict as today (line 201-208).
2. After the loop: `for agent_id, container in self.agents.items(): container.wire_delegation(self.get_agent)`.
3. Continue with consolidation/scheduler wiring.

## Q3 — Tool delivery to one-shot: full toolkit, no RAG

`RunAgentOneShot` does NOT apply RAG-based tool selection. It calls `ToolRegistry.get_schemas()` (returning all registered tool schemas for the child agent) and passes the full list to `run_tool_loop`.

**Why:** specialist agents have small curated toolkits (5-10 tools). RAG filtering adds latency and complexity without filtering anything meaningful. The curation happens at AGENT DESIGN TIME (which tools the agent has), not at REQUEST TIME.

**Implementation:** inside `RunAgentOneShotUseCase.execute`, use:
```python
tool_schemas = self._tool_registry.get_schemas()
```
instead of the RAG-based `get_schemas_relevant(query_vec, top_k=...)` that `RunAgentUseCase` uses.

`RunAgentUseCase` (conversational) is UNCHANGED — it continues to use RAG when configured. The divergence is intentional: conversational agents may have large toolkits and benefit from RAG; specialist agents do not.

**No new port method is needed.** `IToolExecutor.get_schemas()` already exists. The `get_schemas_by_names()` method proposed in an earlier iteration of this design is NO LONGER NEEDED — it has been removed from scope.

**Flow**:
```
DelegateTool.execute
  ├─ validate agent_id in registry       → unknown_agent
  ├─ validate target in allowed_targets  → target_not_allowed
  └─ await RunAgentOneShot.execute(...)  → may return structured error (timeout, max_iter, result_parse_error) OR success
```

Note: `RunAgentOneShot.execute` filters out the `"delegate"` tool from the full schemas before passing to the child LLM — recursion is impossible by construction (see Q1).

## Q4: _tool_loop.py boundary

**Module location**: `core/use_cases/_tool_loop.py`. Underscore prefix signals "private to use_cases package". Both `run_agent.py` and `run_agent_one_shot.py` import from it; nothing outside `core/use_cases/` should.

**Shape**: a **function**, not a class. The loop is stateless across calls — its only state (`failure_counts`, `tripped`, `working_messages`) lives inside one invocation. A class would add ceremony without benefit.

**Delegation-agnostic**: `_tool_loop.py` knows nothing about depth, recursion, or delegation concepts. It is a pure, shared helper. Recursion prevention is handled entirely in `RunAgentOneShot` via schema filtering (see Q1), not here.

**Exact signature**:
```python
async def run_tool_loop(
    *,
    llm: ILLMProvider,
    tools: IToolExecutor,
    messages: list[Message],
    system_prompt: str,
    tool_schemas: list[dict],
    max_iterations: int,
    circuit_breaker_threshold: int,
    agent_id: str,  # for logging only
) -> str:
    """
    Runs the LLM + tool-dispatch loop until a tool-less response or max_iterations.
    Returns the final raw LLM response string.
    """
```

**IN the helper**: LLM calls, tool-call extraction, sequential tool dispatch, circuit breaker, `[Resultados de tools]` message assembly, max-iterations fallback, logging.

**OUT of the helper** (stays in the caller use case): history load/persist, digest read, skills RAG, tools RAG, `AgentContext` construction, `system_prompt` building, extra sections injection, `DelegationResult` parsing, timeout wrapping.

**Caller 1 — `RunAgentUseCase.execute`** (replaces current `self._run_with_tools(...)`):
```python
response = await run_tool_loop(
    llm=self._llm,
    tools=self._tools,
    messages=messages,
    system_prompt=system_prompt,
    tool_schemas=tool_schemas,
    max_iterations=self._cfg.tools.tool_call_max_iterations,
    circuit_breaker_threshold=self._cfg.tools.circuit_breaker_threshold,
    agent_id=self._cfg.id,
)
```

**Caller 2 — `RunAgentOneShotUseCase.execute`**:
```python
effective_prompt = system_prompt or self._agent_context.build_system_prompt(self._cfg.system_prompt)
schemas = [s for s in self._tool_registry.get_schemas() if s["name"] != "delegate"]  # full list, no RAG; delegate excluded (REQ-DG-9)
response = await asyncio.wait_for(
    run_tool_loop(
        llm=self._llm,
        tools=self._tools,
        messages=[Message(role=Role.USER, content=task)],
        system_prompt=effective_prompt,
        tool_schemas=schemas,
        max_iterations=max_iterations,
        circuit_breaker_threshold=self._cfg.tools.circuit_breaker_threshold,
        agent_id=self._cfg.id,
    ),
    timeout=timeout_seconds,
)
```

**State**: pure function — no `self`. Max-iterations and circuit-breaker counters are local variables. This makes the helper trivially unit-testable with mock `ILLMProvider` and `IToolExecutor`.

**Iteration-limit signaling**: on breach, the helper returns the last raw response (as today). `RunAgentOneShot` detects the breach externally by tracking iterations? No — the helper raises `ToolLoopMaxIterationsError` when it would have silently fallen through. `RunAgentOneShot` catches it and maps to a structured `"max_iterations_exceeded"` result; `RunAgentUseCase` catches it too and falls back to returning the last response (preserving today's behavior). This is a **small behavior refinement**: today `run_agent.py` silently returns the last `raw`; we keep that path by catching and returning `raw`. The helper stores the last response on the exception: `ToolLoopMaxIterationsError(last_response: str)`.

## Canonical reason strings

All `DelegationResult.reason` values MUST use these exact literals. No synonyms, no variations. Implementation and tests reference this table.

| Failure mode | `reason` string |
|---|---|
| Unknown `agent_id` (not in registry) | `unknown_agent` |
| Target not in `allowed_targets` | `target_not_allowed` |
| Delegation disabled on parent (defensive — should not happen because tool isn't registered) | `delegation_disabled` |
| Child raised exception | `child_exception:<ExceptionType>` |
| Child exceeded `max_iterations_per_sub` | `max_iterations_exceeded` |
| Child exceeded `timeout_seconds` | `timeout` |
| Child result has no trailing ```json block | `result_parse_error` |
| Child result ```json block is invalid JSON | `result_parse_error` |

Notes:
- `child_exception:<ExceptionType>` uses the Python class name, e.g. `child_exception:ValueError`. Message goes in `details`, not `reason`.
- `result_parse_error` is ONE string; both missing-block and invalid-JSON map to it. The spec's earlier `"malformed_result"` wording is deprecated — use `result_parse_error` only.
- `max_iterations_exceeded` is the canonical form (NOT `max_iterations`).

## Risks & assumptions

- **Assumption**: `AgentContainer.__init__` can safely stash `global_config` and `registry` refs. Verified — no circular serialization, no dataclass `__post_init__` surprises.
- **New concern**: `ToolLoopMaxIterationsError` is a behavior refinement — today `run_agent.py` silently returns the last raw response on max-iter. The refactor must preserve that behavior in `RunAgentUseCase` (catch + return). Missing this catch will break the 13 existing tests in `test_run_agent_basic.py`. Flag this as a must-verify in `sdd-apply`.
- **New concern**: `DelegateTool` holds a reference to the parent's `AgentConfig`. If agents are hot-reloaded in the future, that reference must be refreshed. Out of scope now, but noted.
- **Open**: should `RunAgentOneShot` own the `asyncio.wait_for` timeout wrapper, or should `DelegateTool`? Decision: `DelegateTool` owns it. Rationale: only delegation needs hard timeouts today; keeping it at the delegation boundary matches REQ-DG-6 ("all failures return DelegationResult"). `RunAgentOneShot` stays timeout-agnostic and will raise `asyncio.TimeoutError` if wrapped externally.
- **Schema filter maintenance**: Because recursion prevention is a filter on tool schemas, any future code path that builds schemas for a one-shot execution must also apply this filter. The filter is centralized in `RunAgentOneShot` — one place, not replicated at call sites. The `get_schemas()` call already returns all tools; the filter is a single list comprehension removing `"delegate"`.
- **Full toolkit assumption**: `RunAgentOneShot` passes `get_schemas()` (full list, minus `"delegate"`). If a specialist agent is misconfigured with an excessively large toolkit, this surfaces as an LLM quality issue, not a system failure. Toolkit curation is a design-time responsibility.
