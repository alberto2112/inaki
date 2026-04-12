# Tasks: Webhook Trigger

## Phase 1: Domain Model

- [ ] 1.1 Add `WEBHOOK = "webhook"` to `TriggerType` enum in `core/domain/entities/task.py`
- [ ] 1.2 Create `WebhookPayload(BaseModel)` in `core/domain/entities/task.py` with fields: `type: Literal["webhook"]`, `url: str`, `method: str = "POST"`, `headers: dict[str, str] = {}`, `body: str | None = None`, `timeout: int = 30`, `success_codes: list[int] = [200, 201, 202, 204]`
- [ ] 1.3 Add `WebhookPayload` to the `TriggerPayload` discriminated union in `core/domain/entities/task.py`

## Phase 2: Adapter

- [ ] 2.1 Create `HttpCallerAdapter` class in `adapters/outbound/scheduler/dispatch_adapters.py` with `async def call(self, payload: WebhookPayload) -> str` — uses `httpx.AsyncClient` per-call context manager, raises `RuntimeError` on non-success status codes
- [ ] 2.2 Add `http_caller: HttpCallerAdapter` field to `SchedulerDispatchPorts` dataclass in `adapters/outbound/scheduler/dispatch_adapters.py`

## Phase 3: Dispatch Routing & Wiring

- [ ] 3.1 Import `WebhookPayload` in `core/domain/services/scheduler_service.py`, add `isinstance(payload, WebhookPayload)` branch in `_dispatch_trigger()` calling `self._dispatch.http_caller.call(payload)`
- [ ] 3.2 Import `HttpCallerAdapter` in `infrastructure/container.py`, add `http_caller=HttpCallerAdapter()` to `SchedulerDispatchPorts` construction (~line 384-388)

## Phase 4: Tests

- [ ] 4.1 Unit test `WebhookPayload` — validate defaults, custom fields, JSON round-trip with discriminator in `tests/unit/domain/test_webhook_payload.py`
- [ ] 4.2 Unit test `HttpCallerAdapter.call()` — mock `httpx.AsyncClient`: success (200 + body), non-success code (500 → RuntimeError), timeout (httpx.TimeoutException → raises), custom headers passed through. Create `tests/unit/adapters/scheduler/test_dispatch_adapters.py`
- [ ] 4.3 Unit test webhook dispatch in `tests/unit/domain/test_scheduler_service.py` — mock `dispatch.http_caller`, verify `call()` invoked with correct payload, verify return value propagated to `_finalize_task`

## Phase 5: Documentation

- [ ] 5.1 Add `webhook` section to `docs/scheduler-spec.md` under "5. Tipos de trigger" — payload fields table, example JSON, dispatch description
