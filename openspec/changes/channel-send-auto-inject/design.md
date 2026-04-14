# Design: channel-send-auto-inject

## Architecture

### ChannelContext Value Object

**Location**: `core/domain/value_objects/channel_context.py` (new file)

Lives in `core/` because it's a domain concept — the identity of the conversation channel. Both the use case layer (`RunAgentUseCase`) and domain entities need to reference it. Placing it in `core/domain/value_objects/` follows the existing pattern (`agent_context.py`, `delegation_result.py`, `embedding.py`).

```python
from __future__ import annotations

from pydantic import BaseModel, computed_field


class ChannelContext(BaseModel):
    """
    Identidad del canal de la conversación actual.

    Creado por el adaptador inbound al recibir un mensaje.
    Inyectado en SchedulerTool para auto-completar el target
    de channel_send sin intervención del LLM.
    """

    channel_type: str   # "telegram", "cli", "rest", "daemon"
    user_id: str        # e.g. "123456" (Telegram chat_id), "local" (CLI)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def routing_key(self) -> str:
        """Clave de ruteo para ChannelSendPayload.target: '{channel_type}:{user_id}'."""
        return f"{self.channel_type}:{self.user_id}"
```

**Lifecycle**: One `ChannelContext` instance per conversation turn. Created by the inbound adapter, set on a **shared mutable holder** on `AgentContainer` before calling `run_agent.execute()`. Read by `SchedulerTool._create()` to inject `target`. Never mutated after creation — replaced atomically per turn.

**Thread-safety**: The system runs a single asyncio event loop (Raspberry Pi 5, single process). Within a coroutine turn, context is set before `execute()` and read during tool execution — no concurrent mutation. The holder is a simple `Optional[ChannelContext]` attribute on `AgentContainer`, not a `ContextVar`, because the simpler approach works given the single-loop architecture and avoids hidden coupling.

### Data Flow

```
1. Inbound adapter receives message
   ├── Telegram: channel_type="telegram", user_id=str(chat_id)
   ├── CLI:      channel_type="cli",      user_id="local"
   ├── REST:     channel_type="rest",     user_id="api"
   └── Daemon:   (no user messages — only scheduler dispatch)

2. Inbound adapter sets context on AgentContainer:
   container.set_channel_context(ChannelContext(channel_type=..., user_id=...))

3. container.run_agent.execute(user_input) → tool loop runs

4. LLM calls scheduler tool with trigger_type="channel_send"
   └── trigger_payload: {"text": "Recordar comprar leche"}
       (NO channel_id / target — LLM only provides text + optional user_id)

5. SchedulerTool._create():
   a. Reads self._get_channel_context() → ChannelContext | None
   b. If channel_send and context is None → error (can't auto-inject)
   c. Computes target:
      - If LLM provided user_id override → f"{context.channel_type}:{llm_user_id}"
      - Else → context.routing_key
   d. Injects target into trigger_payload_raw before validation
   e. Strips target/user_id from LLM-visible kwargs

6. ScheduledTask stored in SQLite with trigger_payload.target = "telegram:123456"

7. At dispatch time (scheduler fires):
   SchedulerService._dispatch_trigger → ChannelSenderAdapter.send_message(payload.target, text)

8. ChannelSenderAdapter parses target prefix → routes to correct gateway
   └── "telegram:123456" → telegram_bot.send_message(123456, text)
```

## Component Changes

### 1. `core/domain/entities/task.py` — ChannelSendPayload

Rename `channel_id` → `target`. Add optional `user_id` for LLM override tracking (stored for audit, not used at dispatch).

```python
class ChannelSendPayload(BaseModel):
    type: Literal["channel_send"] = "channel_send"
    target: str          # routing key: "{channel_type}:{user_id}" — auto-injected, never from LLM
    text: str
    user_id: str | None = None  # optional: LLM-provided override of the target user
```

**Why `user_id` is stored**: When the LLM says "remind user X", we want to record that it was an explicit override vs. the default. At dispatch time, only `target` matters.

### 2. `core/domain/value_objects/channel_context.py` — NEW

Full definition shown above in the Architecture section. Frozen Pydantic model, `computed_field` for `routing_key`.

### 3. `adapters/outbound/tools/scheduler_tool.py` — SchedulerTool

#### Constructor change

Accept a callable to retrieve channel context (same pattern as `agent_id` injection — resolved at construction, used at call time):

```python
def __init__(
    self,
    *,
    schedule_task_uc: ISchedulerUseCase,
    agent_id: str,
    user_timezone: str,
    get_channel_context: Callable[[], ChannelContext | None],
) -> None:
    self._uc = schedule_task_uc
    self._agent_id = agent_id
    self._user_timezone = user_timezone
    self._get_channel_context = get_channel_context
```

**Why a callable instead of the object directly**: `SchedulerTool` is constructed once at startup (phase 3 wiring). The `ChannelContext` changes per conversation turn. A callable defers resolution to call time, keeping the tool stateless and testable (inject a lambda in tests).

#### `_create()` injection logic

After validating `trigger_type_raw == "channel_send"`, before calling `ChannelSendPayload.model_validate()`:

```python
if trigger_type_raw == "channel_send":
    ctx = self._get_channel_context()
    if ctx is None:
        return self._error(
            "No se puede crear una tarea channel_send sin contexto de canal. "
            "Este comando solo funciona desde un canal activo (Telegram, REST, etc.)."
        )
    # LLM may optionally override user_id
    llm_user_id = trigger_payload_raw.pop("user_id", None)
    if llm_user_id:
        target = f"{ctx.channel_type}:{llm_user_id}"
    else:
        target = ctx.routing_key
    trigger_payload_raw["target"] = target
    # Strip any channel_id the LLM might have hallucinated
    trigger_payload_raw.pop("channel_id", None)
    # Store user_id override for audit
    if llm_user_id:
        trigger_payload_raw["user_id"] = str(llm_user_id)
```

#### `_update()` trigger_payload handling

Same injection for updates when `trigger_type_str == "channel_send"`: read context, inject `target`, strip `channel_id`.

#### `parameters_schema` update

Update the `trigger_payload` description to remove `channel_id` and document the new behavior:

```python
"trigger_payload": {
    "type": "object",
    "description": (
        "Action-specific payload. "
        "For 'channel_send': {\"text\": \"...\", \"user_id\": \"...\" (optional)}. "
        "The target channel is auto-detected — do NOT provide channel_id or target. "
        "For 'agent_send': {\"agent_id\": \"...\", \"task\": \"...\"}. "
        "For 'shell_exec': {\"command\": \"...\", \"working_dir\": null, "
        "\"env_vars\": {}, \"timeout\": null}."
    ),
},
```

### 4. `infrastructure/container.py` — Wiring

#### AgentContainer changes

Add a mutable `_channel_context` attribute and a setter/getter:

```python
class AgentContainer:
    def __init__(self, agent_config: AgentConfig, global_config: GlobalConfig) -> None:
        # ... existing init ...
        self._channel_context: ChannelContext | None = None

    def set_channel_context(self, ctx: ChannelContext | None) -> None:
        """Establecer contexto del canal para el turno actual. Llamado por adaptadores inbound."""
        self._channel_context = ctx

    def get_channel_context(self) -> ChannelContext | None:
        """Leer contexto del canal actual. Usado por SchedulerTool."""
        return self._channel_context
```

#### `wire_scheduler()` change

Pass the getter callable to `SchedulerTool`:

```python
def wire_scheduler(self, schedule_task_uc: ScheduleTaskUseCase, user_timezone: str) -> None:
    from adapters.outbound.tools.scheduler_tool import SchedulerTool

    self._tools.register(
        SchedulerTool(
            schedule_task_uc=schedule_task_uc,
            agent_id=self.agent_config.id,
            user_timezone=user_timezone,
            get_channel_context=self.get_channel_context,
        )
    )
```

**Import note**: `ChannelContext` is NOT imported in `container.py` at module level — the setter accepts `ChannelContext | None` via a TYPE_CHECKING guard. The actual import is at the inbound adapter call site. This keeps `container.py` lean and avoids circular imports (not that there would be one, but follows the existing pattern).

### 5. Inbound Adapters — Setting ChannelContext

Each inbound adapter sets `ChannelContext` on the container BEFORE calling `run_agent.execute()`.

#### `adapters/inbound/telegram/bot.py`

```python
from core.domain.value_objects.channel_context import ChannelContext

async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... existing auth check ...

    self._container.set_channel_context(
        ChannelContext(
            channel_type="telegram",
            user_id=str(update.effective_user.id),
        )
    )

    try:
        response = await self._container.run_agent.execute(user_input)
        # ...
    finally:
        # Clear context after the turn completes (defensive cleanup)
        self._container.set_channel_context(None)
```

#### `adapters/inbound/cli/cli_runner.py`

```python
from core.domain.value_objects.channel_context import ChannelContext

async def run_cli(app: AppContainer, agent_id: str) -> None:
    container = app.get_agent(agent_id)
    # Set once — CLI context is stable for the session
    container.set_channel_context(
        ChannelContext(channel_type="cli", user_id="local")
    )
    # ... rest of the interactive loop unchanged ...
```

**Note**: CLI sets context once at session start since the channel identity doesn't change. `channel_type="cli"` and `user_id="local"` means `target="cli:local"`. At dispatch time, `ChannelSenderAdapter` would need a `cli` handler (currently only `telegram` is supported). For now, creating a `channel_send` task from CLI will succeed at creation (context exists), but fail at dispatch unless a CLI sender is added. This is acceptable — the scheduler will mark the task as FAILED and log the error.

#### `adapters/inbound/rest/routers/agents.py`

```python
from core.domain.value_objects.channel_context import ChannelContext

@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, container: AgentContainer = Depends(get_container)) -> ChatResponse:
    container.set_channel_context(
        ChannelContext(channel_type="rest", user_id="api")
    )
    try:
        response = await container.run_agent.execute(body.message)
    finally:
        container.set_channel_context(None)
    return ChatResponse(agent_id=cfg.id, agent_name=cfg.name, response=response)
```

**Future**: REST could accept `user_id` from a header or request body field to enable per-user routing. Out of scope for now.

#### `adapters/inbound/daemon/runner.py`

No change needed. The daemon runner doesn't initiate user conversations — it starts channels (Telegram bots, REST servers) that set their own context. The daemon itself only calls `app_container.startup()` / `shutdown()` for the scheduler lifecycle.

### 6. `adapters/outbound/scheduler/dispatch_adapters.py` — ChannelSenderAdapter Fix

Two changes:

#### a) Rename `channel_id` → `target` in method signature and parsing

```python
class ChannelSenderAdapter:
    """Rutea el target (prefix:user_id) al gateway correcto."""

    def __init__(self, app_container: Any) -> None:
        self._container = app_container

    async def send_message(self, target: str, text: str) -> None:
        # parse prefix: "telegram:<user_id>"
        prefix, _, user_id = target.partition(":")
        if prefix == "telegram":
            bot = self._container.get_telegram_bot()
            if bot is None:
                raise RuntimeError(
                    f"No hay bot de Telegram configurado para enviar a target '{target}'"
                )
            await bot.send_message(int(user_id), text)
        else:
            raise ValueError(f"Prefijo de canal desconocido: {prefix}")
```

#### b) Fix telegram gateway resolution

The current code references `self._container.telegram_gateway` which doesn't exist on `AppContainer`. The fix depends on how the Telegram bot is accessible.

**Option chosen**: Add a `get_telegram_bot()` method to `AppContainer` that returns the `python-telegram-bot` `Bot` object (not the full `TelegramBot` adapter — just the underlying bot instance for sending messages).

```python
# In AppContainer:
def get_telegram_bot(self):
    """
    Retorna el Bot instance de python-telegram-bot para enviar mensajes.
    Lazy-init: crea un Bot con el token del primer agente que tenga Telegram configurado.
    Retorna None si ningún agente tiene Telegram.
    """
    if hasattr(self, "_telegram_bot"):
        return self._telegram_bot

    for agent_cfg in self.registry.list_all():
        tg_cfg = agent_cfg.channels.get("telegram", {})
        token = tg_cfg.get("token")
        if token:
            from telegram import Bot
            self._telegram_bot = Bot(token=token)
            return self._telegram_bot

    self._telegram_bot = None
    return None
```

**Why lazy-init**: Avoids importing `telegram` at startup if no agents use it. Only materializes on first `channel_send` dispatch.

**Why first-token**: Currently there's one Telegram token for the whole system. If multiple agents had different bots, we'd need per-agent routing — out of scope.

#### c) Update `_dispatch_trigger` in `SchedulerService`

```python
# scheduler_service.py line 203 — change from:
await self._dispatch.channel_sender.send_message(payload.channel_id, payload.text)
# to:
await self._dispatch.channel_sender.send_message(payload.target, payload.text)
```

Same for `AgentSendPayload.output_channel` on line 210 — no change needed there (it uses its own field, not `channel_id`).

## Interface Changes

### LLM-facing `parameters_schema` for channel_send

**Before** (what the LLM sees in trigger_payload description):
```
For 'channel_send': {"channel_id": "...", "text": "..."}
```

**After**:
```
For 'channel_send': {"text": "...", "user_id": "..." (optional)}
```

The LLM provides:
- `text` (required): The message to send
- `user_id` (optional): Override target user (defaults to current conversation user)

The LLM does NOT provide:
- `target` — auto-injected from `ChannelContext.routing_key`
- `channel_id` — removed, silently stripped if hallucinated

### ChannelSendPayload stored in DB

```json
{
  "type": "channel_send",
  "target": "telegram:123456",
  "text": "Recordar comprar leche",
  "user_id": null
}
```

## Migration

None. User will delete old DB (`~/.inaki/scheduler.db`). Existing tasks with `channel_id` field will fail validation on load — acceptable since the user has agreed to drop the DB.

## Test Strategy

Unit tests for each component:

1. **`ChannelContext`**: `routing_key` computed correctly, immutable after creation.
2. **`ChannelSendPayload`**: Validates with `target` field, rejects missing `target`.
3. **`SchedulerTool._create`**: 
   - With context → injects `target` correctly
   - Without context → returns error
   - With LLM `user_id` override → builds correct target
   - Strips hallucinated `channel_id` from LLM input
4. **`SchedulerTool._update`**: Same injection for channel_send payload updates.
5. **`ChannelSenderAdapter.send_message`**: Parses `target` prefix correctly, calls bot.
6. **`AgentContainer.set/get_channel_context`**: Set and retrieve works, None default.
7. **Integration**: Telegram inbound → context set → scheduler tool create → stored target matches expected.
