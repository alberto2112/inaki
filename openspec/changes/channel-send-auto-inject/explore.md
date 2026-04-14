# Exploration: channel-send-auto-inject

## Problem Statement

The LLM cannot know which channel the user is communicating through. Currently `ChannelSendPayload` requires a `channel_id` field which the LLM has to guess (it invents values like "default"). The LLM should only specify WHAT to send; the system should handle WHERE to send it.

**Agreed design target:**
- LLM specifies: `text` (required) + `user_id` (optional, defaults to current user)
- System injects: `channel_id` from the current conversation channel at task creation time (same pattern as `created_by` in SchedulerTool)

---

## Relevant Code Locations

| Component | File | Lines |
|-----------|------|-------|
| `ChannelSendPayload` definition | `core/domain/entities/task.py` | 36–39 |
| `ScheduledTask` entity | `core/domain/entities/task.py` | 89–106 |
| `SchedulerTool.__init__` | `adapters/outbound/tools/scheduler_tool.py` | 166–175 |
| `SchedulerTool._create` (created_by injection pattern) | `adapters/outbound/tools/scheduler_tool.py` | 202–299 |
| `SchedulerTool.parameters_schema` (LLM-facing schema) | `adapters/outbound/tools/scheduler_tool.py` | 93–164 |
| `SchedulerTool._update` (trigger_payload update path) | `adapters/outbound/tools/scheduler_tool.py` | 366–469 |
| `wire_scheduler` | `infrastructure/container.py` | 209–225 |
| `AppContainer` scheduler wiring | `infrastructure/container.py` | 405–432 |
| `ChannelSenderAdapter.send_message` | `adapters/outbound/scheduler/dispatch_adapters.py` | 15–27 |
| `SchedulerService._dispatch_trigger` | `core/domain/services/scheduler_service.py` | 200–220 |
| `TelegramBot._handle_message` (has user_id) | `adapters/inbound/telegram/bot.py` | 78–113 |
| `cli_runner.run_cli` (no channel identity) | `adapters/inbound/cli/cli_runner.py` | 23–101 |
| REST `/chat` endpoint (no channel identity) | `adapters/inbound/rest/routers/agents.py` | 36–47 |
| Daemon runner (Telegram + REST startup) | `adapters/inbound/daemon/runner.py` | 47–131 |

---

## Current ChannelSendPayload

```python
# core/domain/entities/task.py lines 36-39
class ChannelSendPayload(BaseModel):
    type: Literal["channel_send"] = "channel_send"
    channel_id: str   # e.g. "telegram:123456" — LLM must guess this!
    text: str
```

The `channel_id` format is `"telegram:<user_id>"` (parsed in `ChannelSenderAdapter`).

---

## The created_by Injection Pattern (to follow)

`created_by` is injected by `SchedulerTool` without the LLM ever providing it:

1. **Construction** (`wire_scheduler`, container.py:209–225): `SchedulerTool(agent_id=self.agent_config.id, ...)` — `agent_id` stored as `self._agent_id`.
2. **Create** (`_create`, scheduler_tool.py:284): `created_by=self._agent_id` — always from `self`, never from `kwargs`.
3. The LLM-facing `parameters_schema` has no `created_by` field — it's invisible to the LLM.

**channel_id injection should follow the exact same pattern.**

---

## Current Data Flow: channel_send

```
1. LLM CREATION TIME (SchedulerTool._create):
   LLM kwargs → {"trigger_payload": {"channel_id": "???", "text": "..."}}
   ChannelSendPayload.model_validate(payload_raw)  ← channel_id required, LLM must guess
   ScheduledTask stored in SQLite with channel_id embedded in trigger_payload JSON

2. SCHEDULER FIRE TIME (SchedulerService._dispatch_trigger):
   task.trigger_payload.channel_id → ChannelSenderAdapter.send_message(channel_id, text)

3. DISPATCH ROUTING (ChannelSenderAdapter.send_message):
   channel_id = "telegram:123456"
   prefix, target = "telegram", "123456"
   self._container.telegram_gateway.send_message(int(target), text)
                   ↑
                   BUG: telegram_gateway not defined on AppContainer (pre-existing issue)
```

---

## What Information Is Available at Each Stage

| Stage | Available | Not Available |
|-------|-----------|---------------|
| `wire_scheduler` startup | `agent_id`, `user_timezone` | channel_id (no conversation yet) |
| `SchedulerTool._create` call | LLM kwargs, `agent_id` | channel_id (not passed to tool) |
| `TelegramBot._handle_message` | `update.effective_user.id` → `f"telegram:{user_id}"` | — |
| `run_agent.execute()` call | `user_input` | channel_id (not threaded through) |
| Dispatch time | Only what was persisted in `trigger_payload` | Runtime conversation context |

---

## Pre-existing Bug: telegram_gateway Missing

`ChannelSenderAdapter.send_message` (dispatch_adapters.py:25) calls:
```python
await self._container.telegram_gateway.send_message(int(target), text)
```

`AppContainer` has **no `telegram_gateway` attribute**. `channel_send` dispatch is currently broken for Telegram. This must be fixed as part of this change or tracked separately.

---

## Approaches for Injecting Channel Context

### Approach A: Contextvar (Thread-local async context)
- Use `contextvars.ContextVar[str | None]` to carry `channel_id`
- Inbound adapters set it before calling `run_agent.execute()`
- `SchedulerTool._create` reads it when building `ChannelSendPayload`
- **Pros**: No interface changes; zero plumbing
- **Cons**: Hidden coupling, hard to test, violates explicit dependency

### Approach B: Shared mutable context holder (recommended)
- `ChannelContext` dataclass: `channel_id: str | None = None`
- Created once per `AgentContainer`, shared between `RunAgentUseCase` and `SchedulerTool`
- `wire_scheduler` passes the same `ChannelContext` instance to `SchedulerTool`
- Inbound adapter sets `channel_id` on it before calling `run_agent.execute()`, or `RunAgentUseCase.execute()` accepts `channel_id=` param and sets it
- **Pros**: Explicit, testable, no contextvar magic, follows existing patterns
- **Cons**: Slight coupling between RunAgentUseCase and SchedulerTool via shared object

### Approach C: Per-request SchedulerTool construction
- Rebuild `SchedulerTool` per conversation with the current `channel_id` baked in
- **Pros**: Cleanest from an immutability standpoint
- **Cons**: Expensive; breaks the current static tool registration model

### Approach D: Extend RunAgentUseCase.execute() to pass context to tools
- `execute(user_input, *, injected_context: dict | None = None)`
- Tool registry / ITool.execute() gains an optional `injected_context` param
- **Pros**: Generalizable for future injections
- **Cons**: Changes ITool interface (all tools affected), larger blast radius

---

## Recommended Approach: B (Shared ChannelContext holder)

**Rationale**: Mirrors how `agent_id` is injected (store at construction, use at call time). Minimal surface change. Testable by setting the holder in unit tests.

**Key changes required:**

1. **`core/domain/entities/task.py`**: Make `ChannelSendPayload.channel_id` optional (`str | None = None`), add `user_id: str | None = None`.

2. **New `ChannelContext` object** (in `core/domain/` or `infrastructure/`): Simple dataclass with `channel_id: str | None`.

3. **`adapters/outbound/tools/scheduler_tool.py`**:
   - Add `channel_context: ChannelContext` to `__init__`
   - In `_create`: inject `channel_id` from `self._channel_context.channel_id` if not provided by LLM; strip `channel_id` from LLM-facing schema
   - In `_update`: same injection when updating trigger_payload
   - Update `parameters_schema` to remove `channel_id` from `channel_send` description

4. **`infrastructure/container.py`**:
   - Add `self._channel_context = ChannelContext()` in `AgentContainer.__init__`
   - Pass it in `wire_scheduler` → `SchedulerTool`
   - `RunAgentUseCase.execute()` (or the inbound adapter calling it) sets `_channel_context.channel_id` before the tool loop

5. **Inbound adapters**:
   - `TelegramBot._handle_message`: before `run_agent.execute(user_input)`, set `container._channel_context.channel_id = f"telegram:{update.effective_user.id}"`
   - CLI runner: no channel_id (None) — `channel_send` creation should return a clear error
   - REST: optionally accept `X-Channel-Id` header or leave as None

6. **`adapters/outbound/scheduler/dispatch_adapters.py`**:
   - Fix `telegram_gateway` gap: `AppContainer` must expose a way to send Telegram messages (or `ChannelSenderAdapter` must hold the Telegram bot application reference directly)

---

## Edge Cases and Complications

1. **CLI has no channel**: `channel_context.channel_id` is `None`. `SchedulerTool._create` should return an error: "channel_send is not supported from CLI — no channel context available."

2. **REST has no inherent channel**: Unless the REST client passes a user identifier, `channel_id` will be None. Same error path as CLI, or REST router sets a synthetic channel_id.

3. **Update path**: `SchedulerTool._update` also validates `trigger_payload` — must apply the same injection logic.

4. **LLM-facing schema**: Remove `channel_id` from the `channel_send` trigger_payload description in `parameters_schema`. Only `text` (and optionally `user_id`) should be visible to the LLM.

5. **Existing stored tasks**: Tasks stored with LLM-guessed `channel_id` values (e.g., "default") will still fail at dispatch — pre-existing issue, not in scope.

6. **telegram_gateway pre-existing bug**: Must be resolved before channel_send can work at all. Options:
   - Store the `telegram.ext.Application` in AppContainer and expose `send_message` from it
   - Create a `TelegramGateway` adapter that wraps the bot application

---

## Status

- [x] Exploration complete
- [ ] Proposal
- [ ] Spec
- [ ] Design
- [ ] Tasks
- [ ] Apply
- [ ] Verify
- [ ] Archive
