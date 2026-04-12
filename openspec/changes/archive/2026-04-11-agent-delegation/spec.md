# Spec: agent-delegation

## Capability: agent-one-shot-execution

### Purpose

`agent-one-shot-execution` is a stateless execution path that runs an agent for a single task with no history loaded, no history persisted, and no memory digest read. It is consumed internally — today by `agent-delegation`, and in the future by any use case that needs a clean, isolated agent run without side-effects on persistent state.

### Requirements

#### REQ-OS-1: Clean context execution

The one-shot execution MUST run the agent with an empty message history. It MUST NOT load existing chat history from the repository. It MUST NOT persist the resulting messages. It MUST NOT read the memory digest. The child agent starts with a clean slate on every invocation.

#### Scenario: One-shot does not load or persist history

- GIVEN agent "researcher" exists with 10 persisted chat messages
- WHEN `RunAgentOneShotUseCase.execute` is called with task "summarize this"
- THEN the chat history MUST remain at exactly 10 messages after execution
- AND the agent's response MUST be returned to the caller

#### Scenario: One-shot does not read memory digest

- GIVEN agent "researcher" has a saved memory digest
- WHEN `RunAgentOneShotUseCase.execute` is called
- THEN the digest MUST NOT be injected into the agent's system prompt or messages

---

#### REQ-OS-2: System prompt override

When `system_prompt` is `None`, the one-shot execution MUST use the agent's default system prompt. When `system_prompt` is a non-empty string, it MUST replace the agent's default system prompt entirely.

#### Scenario: Default system prompt used when override is None

- GIVEN agent "researcher" has default system prompt "You are a researcher"
- WHEN `RunAgentOneShotUseCase.execute` is called with `system_prompt=None`
- THEN the agent runs with "You are a researcher" as its system prompt

#### Scenario: Override replaces default system prompt

- GIVEN agent "researcher" has default system prompt "You are a researcher"
- WHEN `RunAgentOneShotUseCase.execute` is called with `system_prompt="You are a classifier"`
- THEN the agent runs with "You are a classifier" as its system prompt
- AND the default system prompt MUST NOT appear in the final prompt

---

#### REQ-OS-3: Iteration and timeout limits

`RunAgentOneShotUseCase.execute` MUST accept `max_iterations` and `timeout_seconds` parameters. When the agent exceeds `max_iterations`, execution MUST stop and return a structured error result. When execution exceeds `timeout_seconds`, it MUST be cancelled and return a structured error result.

#### Scenario: max_iterations limit enforced

- GIVEN `max_iterations=3` is passed to `RunAgentOneShotUseCase.execute`
- WHEN the agent has not completed the task after 3 LLM turns
- THEN execution MUST stop
- AND a structured error result MUST be returned with reason "max_iterations_exceeded"

#### Scenario: timeout_seconds limit enforced

- GIVEN `timeout_seconds=5` is passed to `RunAgentOneShotUseCase.execute`
- WHEN execution exceeds 5 seconds
- THEN execution MUST be cancelled
- AND a structured error result MUST be returned with reason "timeout"

---

#### REQ-OS-4: Full toolkit delivery without RAG filtering

One-shot execution MUST pass the full tool schemas from the child's `ToolRegistry` without applying RAG selection. The child agent receives ALL of its configured tools, regardless of any global RAG-for-tools setting. Rationale: specialist agents have pre-curated toolkits; RAG filtering would be overhead without benefit.

#### Scenario: One-shot execution passes full toolkit without RAG filtering

```gherkin
Scenario: One-shot execution passes full toolkit without RAG filtering
  Given agent "coder" has tools ["read_file", "write_file", "patch_file", "git_tool", "web_search"]
  And a global RAG-for-tools threshold is configured
  When RunAgentOneShot.execute is called for agent "coder"
  Then the tool schemas passed to the child LLM MUST contain all 5 tools
  And no RAG selection MUST have been applied
```

---

## Capability: agent-delegation

### Purpose

`agent-delegation` allows a parent agent to hand off a subtask to a sibling agent via a `delegate` tool. The child runs one-shot and returns a structured, machine-parseable result. This enables agent composition — a coordinator can leverage specialist agents without duplicating their tools or prompts.

### Requirements

#### REQ-DG-1: Delegation opt-in per agent

An agent MUST have `delegation.enabled: true` in its config for the `delegate` tool to be registered. The `delegate` tool MUST NOT appear in the tool schemas of agents without this flag.

#### Scenario: Delegation enabled registers delegate tool

- GIVEN agent "coordinator" has `delegation.enabled: true` in config
- WHEN the agent's tool schemas are assembled
- THEN "delegate" MUST appear in the tool list

#### Scenario: Delegation disabled — no delegate tool

- GIVEN agent "worker" has `delegation.enabled: false` (or the field is absent)
- WHEN the agent's tool schemas are assembled
- THEN "delegate" MUST NOT appear in the tool list

---

#### REQ-DG-2: Target allow-list enforcement

If the parent config has `delegation.allowed_targets` set to a non-empty list, the `delegate` tool MUST reject any `agent_id` not in that list with `status: "failed"`, `reason: "target_not_allowed"`.

#### Scenario: Target in allow-list succeeds

- GIVEN parent has `allowed_targets: ["specialist"]`
- WHEN `delegate` is called with `agent_id="specialist"`
- THEN delegation MUST proceed normally

#### Scenario: Target not in allow-list fails

- GIVEN parent has `allowed_targets: ["specialist"]`
- WHEN `delegate` is called with `agent_id="other_agent"`
- THEN `DelegationResult` MUST be returned with `status: "failed"`, `reason: "target_not_allowed"`

---

#### REQ-DG-3: Unknown target agent failure

When the `agent_id` passed to `delegate` does not exist in the agent registry, the tool MUST return `status: "failed"`, `reason: "unknown_agent"` without raising an exception.

#### Scenario: Unknown agent_id returns structured failure

- GIVEN `agent_id="ghost"` is not registered in the agent registry
- WHEN `delegate` is called with `agent_id="ghost"`
- THEN `DelegationResult` MUST be returned with `status: "failed"`, `reason: "unknown_agent"`

---

#### REQ-DG-4: Structured result contract

The child agent MUST produce a trailing ```json block as its final message containing `status`, `summary`, `details`, and `reason`. The parent MUST parse this block and return it as a `DelegationResult`. `reason` is OPTIONAL (MAY be omitted on success).

#### Scenario: Child produces valid JSON block — parent parses it

- GIVEN child's final message ends with a valid ```json block containing `status: "success"` and `summary: "done"`
- WHEN the parent's `delegate` tool processes the child's response
- THEN `DelegationResult.status` MUST be "success"
- AND `DelegationResult.summary` MUST be "done"

---

#### REQ-DG-5: Result parse failure is a structured failure

When the child's final message contains no trailing ```json block, or the block is invalid JSON, the parent MUST return `status: "failed"`, `reason: "result_parse_error"`, with the raw child response text preserved in `details`.

#### Scenario: Child produces no JSON block

- GIVEN child's final message is plain text with no ```json block
- WHEN the parent's `delegate` tool processes the child's response
- THEN `DelegationResult` MUST have `status: "failed"`, `reason: "result_parse_error"`
- AND `DelegationResult.details` MUST contain the raw text

#### Scenario: Child produces malformed JSON

- GIVEN child's final message ends with a ```json block containing invalid JSON
- WHEN the parent's `delegate` tool processes the child's response
- THEN `DelegationResult` MUST have `status: "failed"`, `reason: "result_parse_error"`
- AND `DelegationResult.details` MUST contain the raw text

---

#### REQ-DG-6: Failure modes always return DelegationResult

All delegation failures (unknown agent, not allowed, child exception, timeout, iteration limit, parse error) MUST be returned as `DelegationResult` objects to the parent LLM loop. The `delegate` tool MUST NOT raise exceptions that propagate to the LLM loop.

#### Scenario: Child exception becomes DelegationResult failure

- GIVEN the child agent raises an unhandled exception during execution
- WHEN the parent's `delegate` tool catches it
- THEN `DelegationResult` MUST be returned with `status: "failed"`, `reason` containing the exception type

#### Scenario: Child timeout becomes DelegationResult failure

- GIVEN `timeout_seconds=5` and the child runs longer than 5 seconds
- WHEN the parent's `delegate` tool catches the timeout
- THEN `DelegationResult` MUST be returned with `status: "failed"`, `reason: "timeout"`

#### Scenario: Child max_iterations becomes DelegationResult failure

- GIVEN `max_iterations_per_sub=3` and the child exceeds 3 iterations
- WHEN the parent's `delegate` tool catches the limit breach
- THEN `DelegationResult` MUST be returned with `status: "failed"`, `reason: "max_iterations_exceeded"`

---

#### REQ-DG-7: Agent discovery section in parent system prompt

When an agent has `delegation.enabled: true`, its system prompt MUST include a dynamically-generated section listing the available target agents (id, description, and available tools) drawn from the agent registry. When `allowed_targets` is set, the section MUST list only those agents.

#### Scenario: Agent discovery present when delegation enabled

- GIVEN agent "coordinator" has `delegation.enabled: true` and no `allowed_targets` restriction
- WHEN the coordinator's system prompt is built
- THEN it MUST contain a section listing all registered sibling agents with id, description, and tools

#### Scenario: Agent discovery filtered by allow-list

- GIVEN agent "coordinator" has `delegation.enabled: true` and `allowed_targets: ["specialist"]`
- WHEN the coordinator's system prompt is built
- THEN the agent discovery section MUST list only "specialist"
- AND other registered agents MUST NOT appear in that section

#### Scenario: Agent discovery absent when delegation disabled

- GIVEN agent "worker" has `delegation.enabled: false`
- WHEN the worker's system prompt is built
- THEN the system prompt MUST NOT contain an agent discovery section

---

#### REQ-DG-8: Child exceptions, timeouts, and iteration breaches map to DelegationResult

(Covered under REQ-DG-6 scenarios.)

---

#### REQ-DG-9: Sub-agents must not receive the delegate tool in their schemas

Sub-agents running via one-shot execution MUST NOT receive the `delegate` tool in their tool schemas. This makes recursive delegation impossible by construction — no runtime check is performed. `RunAgentOneShot` is responsible for filtering out any tool whose name is `"delegate"` when building the schemas list passed to the child LLM.

#### Scenario: Sub-agents do not have access to the delegate tool

- GIVEN agent "researcher" has `delegation.enabled: true`
- AND agent "coder" has `delegation.enabled: true`
- WHEN "researcher" delegates a task to "coder"
- THEN the one-shot execution for "coder" MUST NOT include "delegate" in its tool schemas
- AND "coder" cannot emit a delegate tool call even if its LLM attempts to

---

## Data Contracts

### DelegationResult

```json
{
  "status": "success | failed",
  "summary": "<string — what the child accomplished or attempted>",
  "details": "<string — optional; raw text on parse failure, exception message on child exception>",
  "reason": "<string — omitted on success; failure code on failure>"
}
```

Failure reason values: `"unknown_agent"`, `"target_not_allowed"`, `"result_parse_error"`, `"child_exception"`, `"timeout"`, `"max_iterations_exceeded"`, `"delegation_disabled"`.

### Config Schema

```yaml
# Global config (applies to all agents unless overridden)
delegation:
  max_iterations_per_sub: 10           # Default: agent's own max_iterations
  timeout_seconds: 60                  # Default: no timeout

# Per-agent config (under agents/<id> YAML)
delegation:
  enabled: true                        # REQUIRED to register delegate tool; default false
  allowed_targets:                     # Optional; if absent, all agents are valid targets
    - specialist-agent-id
```

---

## Non-Scenarios

The following are explicitly OUT OF SCOPE for this spec:

- **Parallel / fan-out delegation**: Multiple children invoked concurrently from one `delegate` call.
- **Async fire-and-forget**: Delegating without waiting for the result.
- **Cross-process delegation**: Delegating to a sibling running in a separate process via HTTP/REST.
- **Parent memory inheritance**: Child receiving or reading the parent's digest or history.
- **Streaming child progress**: Parent receiving incremental updates while the child runs.
- **Automatic retries or fallback**: System-level retry on failure; parent LLM decides all recovery.
- **Recursive delegation**: Sub-agents delegating further to other agents. Impossible by construction — the child never receives the `delegate` tool.
- **Dynamic tool selection by parent**: The parent cannot cherry-pick tools from an agent's toolkit. The specialist model precludes it by design. If finer-grained tool access is needed, create a new specialist agent.
- **RAG tool filtering in one-shot execution**: The child uses its full toolkit. RAG selection is not applied inside `RunAgentOneShot`.
