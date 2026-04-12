# Design: Webhook Trigger

## Technical Approach

Follow the established trigger pattern exactly: enum value → Pydantic payload → discriminated union → isinstance routing → adapter. No new architectural patterns introduced.

## Architecture Decisions

### Decision: httpx Client Lifecycle

| Option | Tradeoff | Decision |
|--------|----------|----------|
| Per-call `async with httpx.AsyncClient()` | Slightly slower (new connection each time), simpler lifecycle | **Chosen** |
| Shared client on `SchedulerService` | Faster (connection pooling), requires startup/shutdown lifecycle | Rejected |

**Rationale**: Scheduler tasks fire infrequently (minutes/hours apart). Connection pooling adds lifecycle complexity for negligible gain. Per-call is simpler and matches the fire-and-forget nature of scheduled tasks.

### Decision: URL Field Type

| Option | Tradeoff | Decision |
|--------|----------|----------|
| `str` with no validation | Simple, YAML-friendly, relies on httpx to reject bad URLs at call time | **Chosen** |
| `pydantic.HttpUrl` | Validates at parse time, but serializes as `Url` object — breaks YAML round-trip | Rejected |

**Rationale**: Existing payloads use plain `str` for identifiers (e.g., `channel_id`). httpx raises `InvalidURL` on bad URLs, which feeds into retry. Consistency wins.

### Decision: Adapter Placement

| Option | Tradeoff | Decision |
|--------|----------|----------|
| `HttpCallerAdapter` in `dispatch_adapters.py` alongside others | Co-located, easy to find | **Chosen** |
| New file `http_caller_adapter.py` | Separate concerns, but adds a file for ~15 lines | Rejected |

## Data Flow

```
SchedulerService._dispatch_trigger(task)
    │
    ├── isinstance(payload, WebhookPayload)
    │       │
    │       ▼
    │   dispatch.http_caller.call(payload)
    │       │
    │       ▼
    │   httpx.AsyncClient.request(method, url, headers, content, timeout)
    │       │
    │       ├── 2xx (in success_codes) → return response.text
    │       └── other / timeout / error → raise → retry loop
    │
    └── (existing triggers unchanged)
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `core/domain/entities/task.py` | Modify | Add `WEBHOOK = "webhook"` to `TriggerType`, add `WebhookPayload`, extend `TriggerPayload` union |
| `core/domain/services/scheduler_service.py` | Modify | Add `WebhookPayload` import, add `isinstance` branch calling `self._dispatch.http_caller.call(payload)` |
| `adapters/outbound/scheduler/dispatch_adapters.py` | Modify | Add `HttpCallerAdapter` class, add `http_caller` field to `SchedulerDispatchPorts` |
| `infrastructure/container.py` | Modify | Import `HttpCallerAdapter`, add `http_caller=HttpCallerAdapter()` to `SchedulerDispatchPorts` construction |
| `docs/scheduler-spec.md` | Modify | Add webhook section to trigger types documentation |
| `tests/unit/domain/test_scheduler_service.py` | Modify | Add webhook dispatch test |
| `tests/unit/adapters/scheduler/test_dispatch_adapters.py` | Create | Unit tests for `HttpCallerAdapter` |

## Interfaces / Contracts

```python
# core/domain/entities/task.py
class WebhookPayload(BaseModel):
    type: Literal["webhook"] = "webhook"
    url: str
    method: str = "POST"
    headers: dict[str, str] = {}
    body: str | None = None
    timeout: int = 30
    success_codes: list[int] = [200, 201, 202, 204]

# adapters/outbound/scheduler/dispatch_adapters.py
class HttpCallerAdapter:
    async def call(self, payload: WebhookPayload) -> str:
        """Make HTTP request. Returns response body. Raises on failure."""
        ...
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `WebhookPayload` validation, defaults, serialization | Direct model construction + `model_dump()` |
| Unit | `HttpCallerAdapter.call()` — success, non-success code, timeout, connection error | Mock `httpx.AsyncClient` via `pytest-mock` |
| Unit | `SchedulerService._dispatch_trigger()` — webhook branch | Mock `dispatch.http_caller`, verify `call()` invoked with payload |
| Unit | `TriggerPayload` union — webhook discriminator resolves | JSON round-trip with `type: "webhook"` |

## Migration / Rollout

No migration required. `trigger_payload` is stored as JSON text — existing rows are unaffected. New webhook tasks can be created immediately after deployment via CLI `inaki scheduler edit`.
