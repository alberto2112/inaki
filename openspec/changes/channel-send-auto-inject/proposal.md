# Proposal: channel-send-auto-inject

## Intent

The `channel_send` scheduler trigger requires a `channel_id` routing key (e.g. `"telegram:123456"`) that the LLM cannot know — it's an internal infrastructure detail. This change makes `target` (renamed from `channel_id`) auto-injected from the current conversation's channel context, so the LLM only needs to specify the message text. As a side-effect, this fixes the broken `ChannelSenderAdapter` which references a non-existent `telegram_gateway` attribute on `AppContainer`.

## Scope

**IN scope:**
- Rename `channel_id` → `target` in `ChannelSendPayload`
- Create a `ChannelContext` value object in `core/domain/entities/` to carry `channel_type` + `user_id` (produces the routing key `"{channel_type}:{user_id}"`)
- Inbound adapters (Telegram, CLI, REST, daemon) set `ChannelContext` on each request
- `SchedulerTool._create` reads `ChannelContext` and injects `target` into the payload — LLM never provides it
- `user_id` field optional in the LLM-facing schema — defaults to current user from channel context, but LLM can override
- Fix `ChannelSenderAdapter.send_message()` to use the correct gateway reference from `AppContainer`
- Update `infrastructure/container.py` wiring to thread `ChannelContext` through

**OUT of scope:**
- Multi-channel fan-out (send to Telegram AND REST simultaneously)
- New channel types beyond what already exists
- Changes to `AgentSendPayload` or other trigger types
- Migration of existing scheduled tasks in SQLite (old `channel_id` values)

## Approach

Follow the established `created_by` injection pattern already used in `SchedulerTool`:

1. **Domain entity**: Add `ChannelContext(channel_type: str, user_id: str)` as a small Pydantic model in `core/domain/entities/`. It exposes a `routing_key` property → `"{channel_type}:{user_id}"`.

2. **Payload rename**: `ChannelSendPayload.channel_id` → `ChannelSendPayload.target`. The field is still a `str` routing key internally, but it's no longer exposed to the LLM.

3. **Inbound adapters set context**: Each inbound adapter (Telegram bot, CLI, REST, daemon) creates a `ChannelContext` and attaches it to the agent container or passes it through the call chain. For Telegram: `channel_type="telegram"`, `user_id=str(chat_id)`. For CLI: `channel_type="cli"`, `user_id="local"`.

4. **SchedulerTool injection**: In `_create()`, read `ChannelContext` from the container/context, compute `target = channel_context.routing_key`, and inject it into the trigger payload dict before validation. Same pattern as `created_by=self._agent_id`.

5. **LLM schema**: Remove `channel_id` from the tool's JSON schema for `channel_send`. Add optional `user_id` (defaults to current user). The LLM only provides `text` and optionally `user_id`.

6. **Fix ChannelSenderAdapter**: Replace `self._container.telegram_gateway` with the correct attribute path. Wire the Telegram gateway into `AppContainer` so it's accessible at dispatch time.

## Key Changes

- **`core/domain/entities/task.py`** — Rename `ChannelSendPayload.channel_id` → `target`. Add optional `user_id` field.
- **`core/domain/entities/channel_context.py`** (new) — `ChannelContext` Pydantic model with `channel_type`, `user_id`, and `routing_key` property.
- **`adapters/outbound/tools/scheduler_tool.py`** — Accept `ChannelContext` in constructor. In `_create()`, inject `target` from context. Update LLM-facing JSON schema to remove `channel_id`, add optional `user_id`.
- **`adapters/inbound/telegram/bot.py`** — Create `ChannelContext(channel_type="telegram", user_id=str(chat_id))` and pass it through.
- **`adapters/inbound/cli/`** — Create `ChannelContext(channel_type="cli", user_id="local")`.
- **`adapters/inbound/rest/`** — Create `ChannelContext` from request metadata.
- **`adapters/inbound/daemon/`** — Create `ChannelContext(channel_type="daemon", user_id="system")`.
- **`adapters/outbound/scheduler/dispatch_adapters.py`** — Fix `ChannelSenderAdapter.send_message()` to use correct gateway reference. Update to parse `target` instead of `channel_id`.
- **`infrastructure/container.py`** — Wire `ChannelContext` into `SchedulerTool` constructor. Ensure Telegram gateway is accessible from `AppContainer` for dispatch.

## Risks

1. **Existing scheduled tasks** — Tasks already stored in SQLite have `channel_id` in their `trigger_payload` JSON. After rename to `target`, dispatching old tasks will fail unless we handle backward compatibility (alias or migration).
2. **ChannelContext propagation** — Threading context through the call chain requires touching every inbound adapter. If one is missed, `channel_send` scheduling will fail silently or raise at runtime.
3. **Daemon/cron context** — When the scheduler dispatches a `channel_send` task, there's no "current conversation" — the context was captured at creation time. Must ensure the stored `target` is self-contained and doesn't depend on runtime context at dispatch time.
4. **Telegram gateway availability** — The gateway fix depends on understanding how `AppContainer` currently holds (or doesn't hold) the Telegram bot instance. May need to lazy-init or handle the case where Telegram is not configured.

## Out of Scope

- Database migration for existing `channel_id` → `target` rename in stored tasks
- Supporting multiple simultaneous channel targets per scheduled task
- Adding new channel types (e.g., Discord, Slack)
- Changes to non-`channel_send` trigger types
- End-to-end integration tests for Telegram dispatch (requires bot token)
