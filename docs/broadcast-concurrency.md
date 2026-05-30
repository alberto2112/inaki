# Concurrency in Multi-Agent Groups — Problem and Pending Solution

> **Note**: this document describes the timing of **broadcast as context** (buffer
> that feeds the system prompt of the next turn). With the addition of
> **broadcast as trigger** (bot-to-bot), a bot also fires its pipeline upon
> any received broadcast message — the LLM decides whether to respond or emit `[SKIP]`.
> That path does NOT go through the Telegram privacy flow — it is independent and described at the end.

## The problem: broadcast context timing

When two Inaki instances (`A` and `B`) are in the same Telegram group with
`behavior: autonomous`, both receive the same user message almost at the same time.

```
User → message → [Telegram]
                    ├── → Bot A (starts processing)
                    └── → Bot B (starts processing)
```

Each bot reads the `BroadcastBuffer` at the start of `_run_pipeline` to build the
`extra_sections` of the system prompt. At that point the other bot's buffer is still empty
(or contains context from previous turns, not the current one). Both bots produce their
responses without seeing each other.

After responding, each one emits via TCP to the other:

```
Bot A responds → emit(BroadcastMessage) → Bot B buffer
Bot B responds → emit(BroadcastMessage) → Bot A buffer
```

Result: the first simultaneous message lacks cross-context. Subsequent messages
do have it, because the buffer is already populated from the previous turn.

## Why there is no infinite loop

Three mechanisms prevent it:

1. **Anti-loop in the TCP adapter**: `TcpBroadcastAdapter` discards any message whose
   `agent_id` matches its own. A bot never sees its own broadcast.
2. **Bot messages ignored by Telegram**: the Bot API does not deliver to a bot the messages
   from ANOTHER bot (platform limitation). The lateral broadcast exists precisely to
   compensate for this — but it also means bots don't "reply" to each other from
   Telegram's perspective.
3. **`[SKIP]` in autonomous mode**: when the LLM considers it has nothing useful to
   contribute, it responds exactly `[SKIP]`. The pipeline detects the marker and sends
   nothing nor emits a broadcast.

## Current state: option C (accept the first turn without cross-context)

The decision was to do nothing. Reasoning:

- The buffer always has the context from the **previous** turn of the other bot — only the first
  simultaneous message is "blind."
- In a fluid conversation, the impact is minimal: the bots see each other starting from the second
  exchange.
- It adds no perceptible latency for the user.
- Zero extra complexity.

## Option B: semaphore per `chat_id` (NOT implemented)

### Idea

An `asyncio.Semaphore(1)` per group `chat_id`. While one bot processes a message
from the group, the next message from the same group waits for it to finish. This serializes
processing per chat.

### How it would work

In `_handle_group_message`, before entering the pipeline:

```python
# In __init__:
self._group_semaphores: dict[int, asyncio.Semaphore] = {}

# In _handle_group_message:
sem = self._group_semaphores.setdefault(chat_id, asyncio.Semaphore(1))
async with sem:
    await self._run_pipeline(update, contenido_grupo, chat_type=chat_type)
```

### Why this improves timing

If bot A acquires the semaphore first:

```
Bot A acquires semaphore → processes → responds → emits broadcast → releases semaphore
Bot B was waiting        → acquires semaphore → buffer ALREADY has A's response → processes with context
```

Bot B now SEES A's response before generating its own.

### Tradeoffs

| Aspect | Impact |
|--------|--------|
| Perceived latency | The second bot waits for the first to finish (~2-5s of LLM inference). The user sees both responses with a small delay between them instead of nearly simultaneously. |
| Complexity | ~10 lines. Semaphore dict grows with the number of active groups — not a practical problem. |
| Timeout | If the first bot's pipeline fails without releasing the semaphore, the second one blocks. Mitigation: `asyncio.wait_for` with configurable timeout (`broadcast_pipeline_timeout_seconds: 30`). |
| Fairness | PTB queues updates internally — if many messages arrive in quick succession to the same group, they are processed one at a time. In active groups this can introduce visible backpressure. |
| Effect on `[SKIP]` | If bot A responds `[SKIP]`, it releases the semaphore without emitting a broadcast. Bot B acquires the semaphore but A's buffer remains empty for that turn. Correct behavior — there is nothing to see. |

### Suggested implementation when decided

File to modify: `adapters/inbound/telegram/bot.py`

1. Add `self._group_semaphores: dict[int, asyncio.Semaphore] = {}` in `__init__`.
2. In `_handle_group_message`, after validating `allowed_chat_ids` and before `await self._run_pipeline(...)`:

```python
_sem = self._group_semaphores.setdefault(chat_id, asyncio.Semaphore(1))
try:
    await asyncio.wait_for(_sem.acquire(), timeout=30.0)
except asyncio.TimeoutError:
    logger.warning(
        "Timeout waiting for group semaphore (chat_id=%s, agent=%s) — processing without serialization",
        chat_id,
        self._agent_cfg.id,
    )
else:
    try:
        await self._run_pipeline(update, contenido_grupo, chat_type=chat_type, extra_sections=extra_sections)
        return
    finally:
        _sem.release()
# Fallback if timeout: process without semaphore
await self._run_pipeline(update, contenido_grupo, chat_type=chat_type, extra_sections=extra_sections)
```

3. Consider cleaning old dict entries with a TTL if there are many groups.

## Decision

**Date**: 2026-04-23  
**Chosen**: Option C (no semaphore).  
**Revisit if**: the missing cross-context on the first turn becomes annoying in
practice, or if more than two bots are added to the same group (the collision window increases).

---

## Broadcast-as-Trigger — direct bot-to-bot

In addition to the context buffer (described above), the broadcast channel also acts
as a **trigger**: if bot A emits a broadcast, B fires the complete pipeline and
decides (via LLM) whether to respond to the group or not — without waiting for a user message.

The user_input that B receives is constructed by prefixing the origin agent_id:

```
[anacleto] che inaki, qué hora es?
```

The LLM decides whether to respond or emit exactly `[SKIP]`. There are no
mention filters in the adapter — the decision is 100% the model's.

### Why it is needed

The Telegram Bot API does not deliver messages from OTHER bots. Without this mechanism, B could
never react to a call from A even if A explicitly mentions it in the
group. The broadcast trigger compensates for this platform limitation.

### Anti-loop with two bots

With two bots `A` and `B`, the infinite loop is prevented because:

1. **`[SKIP]` is the first defense**: the autonomous prompt instructs the LLM to
   respond `[SKIP]` when it has nothing useful to contribute. If the model respects
   the instruction, it cuts the turn without emitting a broadcast.
2. **Shared rate limiter**: the same `FixedWindowRateLimiter` (30s, per
   `(agent_id, chat_id)`) also limits bot-to-bot triggers. If the LLM goes wild,
   the rate limiter cuts it.
3. **TCP adapter anti-loop**: a bot never receives its own broadcast
   (filtered by `agent_id` in `TcpBroadcastAdapter`).
4. **Random jitter (1-3s)**: before processing each broadcast, the bot waits
   a random delay. This distributes simultaneous responses over time, gives
   room for the `BroadcastBuffer` to cross-pollinate context from the other bot, and breaks
   bursts. Constants `BROADCAST_TRIGGER_JITTER_{MIN,MAX}_SEC` in
   `adapters/inbound/telegram/bot.py`.

### With 3+ bots — review

With three or more bots, the collision window increases and a cross-conversation can
converge into a slow loop if the LLM doesn't respect `[SKIP]`. Mitigations to consider
when that happens:

- **Hops counter**: propagate a counter in `BroadcastMessage` and cut at N hops.
- **Pre-response jitter**: small random delay to break simultaneity.
- **Rate limiter decay**: lower N responses/window from 5 to 2 or 3.

None of these are implemented — they will be added if it starts happening in practice.
