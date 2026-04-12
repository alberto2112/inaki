# Proposal: Webhook Trigger

## Intent

The scheduler needs to call external HTTP endpoints on schedule — enabling integration with Zapier, n8n, external APIs, or any service exposing a webhook receiver. Currently, outbound communication is limited to Telegram channels and shell commands.

## Scope

### In Scope
- New `webhook` trigger type with configurable URL, method, headers, body, timeout
- `HttpCallerAdapter` using `httpx.AsyncClient`
- Dispatch routing in `SchedulerService._dispatch_trigger()`
- Unit tests for payload, dispatch, and adapter
- Scheduler spec update (delta spec for FR-03 and FR-07)

### Out of Scope
- Inbound webhook (event-driven task triggering via REST endpoint) — separate change
- Request body templating (e.g., injecting task context into body)
- OAuth/mTLS authentication — plain headers cover Bearer tokens and API keys
- Retry with exponential backoff (existing linear retry is sufficient)

## Capabilities

### New Capabilities
None — webhook is an extension of the existing scheduler trigger system.

### Modified Capabilities
- `scheduler-internal`: FR-03 (Typed Trigger Payloads) adds `webhook` type; FR-07 (Trigger Dispatch) adds webhook dispatch rule

## Approach

Follow the established trigger pattern: enum value → payload model → discriminated union → isinstance dispatch → adapter. Use `httpx.AsyncClient` (already a production dependency) with per-call context manager. Response body returned as task output (truncated by existing `output_truncation_size`).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `core/domain/entities/task.py` | Modified | New `TriggerType.WEBHOOK`, `WebhookPayload`, extend `TriggerPayload` union |
| `core/domain/services/scheduler_service.py` | Modified | New `isinstance` branch + call to `HttpCallerAdapter` |
| `adapters/outbound/scheduler/dispatch_adapters.py` | Modified | New `HttpCallerAdapter`, new field in `SchedulerDispatchPorts` |
| `infrastructure/container.py` | Modified | Wire `HttpCallerAdapter` into dispatch ports |
| `docs/scheduler-spec.md` | Modified | Document webhook trigger |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Target URL unreachable / slow | Medium | Configurable timeout per payload (default 30s); existing retry handles transient failures |
| Secrets in headers stored in plaintext in DB | Low | Same pattern as existing `channel_id` — future change can add encrypted fields |

## Rollback Plan

Revert the commit. No DB migration needed — `trigger_payload` is JSON text, and no schema columns change. Existing tasks are unaffected since the new type only matters for new tasks.

## Dependencies

- `httpx` (already in `pyproject.toml` dependencies)

## Success Criteria

- [ ] `WebhookPayload` validates and round-trips through JSON/YAML correctly
- [ ] `SchedulerService` dispatches webhook tasks calling the target URL
- [ ] Non-2xx responses (or those outside `success_codes`) raise and trigger retry
- [ ] Unit tests cover happy path, failure, timeout, and custom headers
- [ ] Existing trigger types unaffected (no regressions)
