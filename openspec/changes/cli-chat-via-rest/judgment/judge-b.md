# Judge B — Code Review: cli-chat-via-rest

**judge_id**: B
**verdict**: APPROVED_WITH_WARNINGS
**skill_resolution**: injected

---

## Critical

None found.

---

## Warning (Real)

### 1. `_cfg` private access survives in public REST router
**File**: `adapters/inbound/rest/routers/agents.py` — lines 31, 40, 64

`get_info`, `chat`, and `get_history` all read `container.run_agent._cfg` directly. The migration to public API (`get_history()`, `clear_history()`) was applied consistently to the new admin chat router and to Telegram, but the existing public-facing REST router (`/agents/{id}/info`, `/agents/{id}/chat`, `/agents/{id}/history`) still accesses the private attribute.

This is the same category of violation as the `_history` accesses that were fixed — accessing a private collaborator of a use case bypasses the hexagonal boundary. It was NOT introduced by this change (it predates it), but the change explicitly migrated `_history` while leaving `_cfg` untouched. Given the project's "no tech debt" rule, leaving this after the migration is a real warning, not theoretical.

**Fix**: add `get_agent_info() -> AgentConfig` (or a DTO) as a public method on `RunAgentUseCase`, and use it in `agents.py`.

---

### 2. Late `rich` import inside hot path
**File**: `adapters/inbound/cli/cli_runner.py` — line 134

```python
from rich.console import Console
console = Console()
with console.status("Pensando...", spinner="dots"):
    reply = client.chat_turn(...)
```

`rich` is imported inside the loop body on every message turn. This is harmless for correctness but causes a module-lookup on each iteration and allocates a new `Console()` per turn. For a Raspberry Pi 5 target, this is a minor but real inefficiency. The import belongs at the top of the file (or at module level if conditional); the `Console()` instance should be created once per REPL invocation.

**Fix**: move `from rich.console import Console` to the top of the file; create `console = Console()` once at the start of `run_cli`.

---

### 3. `ClearResponse` schema is unused dead code
**File**: `adapters/inbound/rest/admin/schemas.py` — lines where `ClearResponse` is defined

`ClearResponse` is defined in schemas but never referenced by any endpoint or import. The `DELETE /admin/chat/history` endpoint returns HTTP 204 with no body. The schema exists but is never used.

**Fix**: remove `ClearResponse` or add `__all__` to make the dead code explicit if it's reserved for future use.

---

## Warning (Theoretical)

### T1. `set_channel_context` is not concurrency-safe across simultaneous chat requests
**File**: `adapters/inbound/rest/admin/routers/chat.py` — lines 79–99

The design spec acknowledges this limitation. Two concurrent `POST /admin/chat/turn` for the same agent will race on `set_channel_context`. This is theoretical for the Pi5 single-user target but is a known design constraint. Flagging as INFO only — clearly marked in the spec.

### T2. `_map_error` with `error_map=None` silently passes through legacy callers with only `DaemonClientError`
**File**: `adapters/outbound/daemon_client.py` — `_map_error`

Legacy callers (`scheduler_reload`, `consolidate`, `inspect`) pass `error_map=None` and get the old `DaemonClientError` behavior, which is correct. The risk is that a future caller forgetting `error_map` will silently get coarser error handling. This is theoretical now; the `error_map=None` sentinel is documented.

---

## Suggestions

### S1. `_resolver_agente` — naming doesn't follow Spanish convention consistently
**File**: `adapters/inbound/rest/admin/routers/chat.py` — line 33

The function name is `_resolver_agente` (noun, not verb). Spanish naming convention for internal functions is typically a verb: `_resolver_agente` → `_obtener_contenedor` or `_resolver_contenedor_agente`. Minor.

### S2. Test `test_agents_maneja_error_de_conexion` asserts loop continues but message assertion is weak
**File**: `tests/unit/adapters/inbound/cli/test_cli_runner_rest.py` — line 293

```python
assert captured.out  # algún output de error
```
The comment "algún output de error" is vague — it passes even if the print is just the welcome message. Asserting that the error text contains something related to the daemon not running would be more meaningful.

### S3. `run_inspect` in `cli_runner.py` / `cli.py` is not covered by tests
**File**: `adapters/inbound/cli/cli_runner.py` — `run_inspect` function; `inaki/cli.py` — `run_inspect`

The `run_inspect` one-shot path was migrated from async to sync (delegates to `client.inspect`) but has no test in the new test files. The spec focused on interactive chat; the inspect path is a minor gap.

---

## Coverage Notes

- Happy-path and error cases for `POST /turn`, `GET /history`, `DELETE /history` are well-covered.
- `GET /admin/agents` is covered for both happy path and auth failure.
- `DaemonClient` chat methods: all error branches tested including `ConnectError`, `TimeoutException`, `404`, `401`, `403`, `5xx`.
- `run_cli` REPL: all specified scenarios covered (UUID uniqueness, exit commands, clear, normal turn, daemon errors, Ctrl+C).
- **Gap**: `run_inspect` one-shot path has no test coverage.
- **Gap**: no test for `chat_turn` with `DaemonAuthError` in the CLI path (REPL doesn't catch `DaemonAuthError` separately — it falls through to `DaemonClientError`, which is functionally correct but untested).
- **Gap**: the `_cfg` private access paths in `adapters/inbound/rest/routers/agents.py` (`/info`, `/chat`) have no migration test asserting the public API is used.

---

## TDD Compliance

**yes** — The test files are organized by task number (1.1, 5.1–5.4, 6.1–6.3, 7.1–7.2, 8.1–8.6, 9.1–9.2) and the test content matches the described spec scenarios. The naming convention (test task X first, then the implementation task) is consistent with the TDD workflow. No evidence of tests written post-hoc to match an already-working implementation was found.

---

## Hexagonal Compliance

**yes, with one pre-existing caveat**

New code (`chat.py`, `deps.py`, `run_agent.py` additions, `daemon_client_port.py`) follows hexagonal architecture strictly:
- `core/` has no imports from `adapters/` or `infrastructure/` in the new additions.
- The new admin chat router accesses `run_agent` exclusively via its public API (`execute()`, `get_history()`, `clear_history()`).
- `IDaemonClient` port is properly extended in `core/ports/`.

Pre-existing violation NOT introduced by this change: `core/use_cases/run_agent.py` imports `AgentConfig` from `infrastructure/config.py` (line 35). This predates the PR and is outside its scope.

Pre-existing violation NOT fixed by this change: `adapters/inbound/rest/routers/agents.py` still accesses `container.run_agent._cfg` at lines 31, 40, 64. The change partially migrated this (history methods) but left `_cfg` access in place.

---

## Tech Debt Introduced

1. **`ClearResponse` dead code** — schema defined but never used (`schemas.py`). Small, but clean-up was not done.
2. **`run_inspect` path untested** — new sync implementation lacks test coverage.
3. **`_cfg` not migrated in `agents.py`** — the partial migration (history migrated, config not) leaves an inconsistency that will require a follow-up task.

---

## Summary

The core feature is solid: auth was correctly extracted to `deps.py` as `check_admin_auth`, all three new endpoints are guarded, the public `get_history`/`clear_history` API on `RunAgentUseCase` is clean, and the CLI REPL correctly delegates all I/O to `IDaemonClient` without touching `AppContainer`. TDD compliance is evident and test coverage for the declared scenarios is comprehensive. The main real warnings are: three surviving `_cfg` private accesses in the existing public REST router (partial migration that left a known inconsistency), and a minor `rich` import inefficiency inside the REPL hot path. No critical violations found.
