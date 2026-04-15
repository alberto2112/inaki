# Code Review — cli-chat-via-rest
## Judge A

---

## verdict
APPROVED_WITH_WARNINGS

---

## critical

_None found._

---

## warning_real

### W1 — `_cfg` private access NOT migrated in `agents.py`
- **File**: `adapters/inbound/rest/routers/agents.py` lines 31, 40, 64
- **Why**: The change migrated `_history` accesses to the public API (`get_history`, `clear_history`), but `container.run_agent._cfg` is still accessed directly in `get_info`, `chat`, and `get_history`. This is a hexagonal boundary violation: adapters must not reach into use-case internals via private attributes. The team flagged this themselves in the review prompt, but did NOT fix it. Per the project's no-tech-debt policy, this must be resolved in this change.
- **Fix**: Expose `agent_id`, `name`, `description` as public properties on `RunAgentUseCase` (or via a dedicated `AgentInfo` value object), then update `agents.py` to use those. Alternatively, expose a `get_info() -> AgentInfo` method.

### W2 — `DaemonAuthError` always hardcodes status_code=401, even when triggered by a 403
- **File**: `core/domain/errors.py` line 109–116; `adapters/outbound/daemon_client.py` line 209
- **Why**: When the server returns HTTP 403 (no auth_key configured), `_map_error` raises `DaemonAuthError()` whose `__init__` hardcodes `status_code=401`. Any code that catches `DaemonAuthError` and inspects `exc.status_code` to distinguish "wrong key" (401) from "no key configured" (403) will get a wrong value. The error message also says "X-Admin-Key" but the issue is the server has no key set.
- **Fix**: Either accept a `status_code` parameter in `DaemonAuthError.__init__`, or use two distinct exceptions (`DaemonAuthInvalidKeyError` / `DaemonAuthNotConfiguredError`). At minimum, don't hardcode 401 when 403 is also mapped to the same class.

### W3 — Vacuous negative assertions in `test_agents_router.py`
- **File**: `tests/unit/adapters/inbound/rest/test_agents_router.py` lines 65 and 98
- **Why**: `mock_run_agent._history.load.assert_not_called()` and `mock_run_agent._history.clear.assert_not_called()` are vacuous on a plain `MagicMock`. Because `mock_run_agent` is not created with `spec=RunAgentUseCase` or `autospec=True`, accessing `mock_run_agent._history` creates a brand-new sub-MagicMock on the fly that was never called — the assertion passes regardless of whether the production code actually accesses `_history` or not. These tests do NOT detect the regression they are supposed to guard against.
- **Fix**: Use `create_autospec(RunAgentUseCase, ...)` for the run_agent mock so that `_history` is not a valid attribute and any access on it would raise `AttributeError`, making the test actually fail if the code regresses.

### W4 — `rich` imported inside hot-path function body without fallback
- **File**: `adapters/inbound/cli/cli_runner.py` line 134
- **Why**: `from rich.console import Console` is inside the `while True` loop, inside the normal-message branch. Python caches module imports so this is not a performance issue per se, but it means if `rich` is missing (it IS declared in pyproject.toml, so this is low probability), every chat turn fails with `ImportError` instead of a clean startup error. More critically, it obscures that `rich` is a runtime dependency of this function. The import should be at module top.
- **Fix**: Move `from rich.console import Console` to the top of the file.

### W5 — `/consolidate` handler in `cli_runner.py` ignores return type
- **File**: `adapters/inbound/cli/cli_runner.py` line 91
- **Why**: `client.consolidate()` returns `dict[str, Any]`, but the code does `print(f"✓ {result}")` — this prints the raw dict repr, not a human-readable message. The test `test_consolidate_200_with_valid_key` asserts `execute` is called but no test in the CLI suite checks what gets printed after `/consolidate`.
- **Fix**: Extract the `resultado` key from the dict (same way `cli.py::consolidate` does `json.dumps(result, ...)`), or document that the dict repr is intentional.

---

## warning_theoretical

### WT1 — Concurrent requests to the same agent race on `set_channel_context`
- **File**: `adapters/inbound/rest/admin/routers/chat.py` lines 80–99
- **Why**: `set_channel_context` is called before `execute` and cleared in `finally`. If two concurrent HTTP requests hit the same agent simultaneously, the second `set_channel_context` call overwrites the first before `execute` returns. The review prompt explicitly marks `set_channel_context` as "design-aware" — this is an acknowledged architectural tradeoff, not a new bug introduced by this change.
- **Note**: INFO only. The Pi5 single-user deployment context means this is extremely unlikely to matter in practice. Not actionable in this change.

### WT2 — `httpx.get`/`httpx.post`/`httpx.delete` create a new connection per call (no connection pool)
- **File**: `adapters/outbound/daemon_client.py` — all three `_post`, `_get`, `_delete` helpers
- **Why**: Using module-level `httpx.get(...)` etc. bypasses connection pooling. For the chat REPL (one request per user turn) this is totally fine. Could matter if the client is called in tight loops.
- **Note**: INFO only. No connection pool needed for current use pattern.

---

## suggestion

### S1 — `_resolver_agente` function name mixes Spanish content with _private underscore convention
- **File**: `adapters/inbound/rest/admin/routers/chat.py` line 33
- **Suggestion**: Rename to `_resolver_agente` is fine, but consider `_get_agent_container` for consistency with Python idiom (the rest of the file uses Spanish for docstrings/variables, but underscore prefixed helpers at module level aren't a public API anyway). Minor.

### S2 — Test `test_get_history_vacia_tras_delete` doesn't verify `clear_history` was actually called
- **File**: `tests/unit/adapters/rest_admin/test_chat_router.py` line 284
- **Why**: The test pre-configures `get_history` to return `[]` before the DELETE call, so it would pass even if DELETE did nothing. A stronger test would configure `get_history` to return non-empty, then after DELETE configure it to return empty, verifying the sequence.
- **Suggestion**: Assert `mock_run_agent.clear_history.assert_awaited_once()` in this test as well.

### S3 — `ClearResponse` schema defined but never used
- **File**: `adapters/inbound/rest/admin/schemas.py` lines 73–77
- **Why**: `ClearResponse` is defined but the DELETE endpoint returns `Response(status_code=204)` with no body. The schema is dead code.
- **Suggestion**: Remove `ClearResponse` or add a comment explaining it's reserved for future use.

### S4 — No test for `list_agents` when the daemon returns an empty list
- **File**: `tests/unit/adapters/outbound/test_daemon_client_chat.py`
- **Suggestion**: Add a test case for `list_agents` returning `{"agents": []}` to verify the empty-list path.

---

## coverage_notes

- **`/consolidate` command output format in CLI**: No test verifies what text is printed to stdout when `/consolidate` succeeds. The tests only verify `DaemonNotRunningError` and `DaemonClientError` handling. The happy-path output (`✓ {result}`) is untested.
- **`/history` command output in CLI**: `test_cli_runner_rest.py` has no test for the `/history` command output when messages exist (only empty list tested in old test file via `chat_history.return_value = []`). The loop that prints `{msg['role']}: {msg['content']}` in `cli_runner.py` line 85 is not covered.
- **`/inspect` command in CLI**: No test exercises the `/inspect <mensaje>` branch of `run_cli`. The `print_inspect` function at line 155 is untested.
- **`DaemonAuthError` triggered by 403 from daemon**: No test in `test_daemon_client_chat.py` for the case where the daemon returns 403 (no auth key configured) — only 401 is tested.

---

## tdd_compliance
**unclear** — Cannot inspect git commit history (changes are in working tree, not committed). However, the test files are organized with task references (e.g., "Cubre tareas 5.1, 5.2, 5.3, 5.4 (TEST)") that suggest a spec-first workflow. The `(TEST)` label pattern implies tests were written as a batch covering spec tasks. Without commit-by-commit history, cannot confirm strict RED-GREEN ordering, but the presence of comprehensive tests covering spec scenarios before implementation details suggests TDD discipline was followed in spirit.

---

## hexagonal_compliance
**Mostly yes — one residual violation in pre-existing file**

- `core/` never imports from `adapters/`: confirmed clean. The `infrastructure/config.py` import in `core/use_cases/run_agent.py` pre-dates this change.
- New code (`chat.py`, `deps.py`, `admin.py`) accesses `AgentContainer` via its public interface only (`run_agent`, `set_channel_context`, `.agents`).
- **Exception**: `adapters/inbound/rest/routers/agents.py` (pre-existing per-agent REST router) still accesses `container.run_agent._cfg` at lines 31, 40, and 64. This was partially addressed by the change (history methods migrated to public API) but `_cfg` was explicitly NOT migrated. This file was modified in this change (history methods), making the residual `_cfg` accesses new tech debt introduced during this change window.

---

## tech_debt_introduced

1. **`_cfg` access in `agents.py`** — `get_info` and `chat` and `get_history` all still read `container.run_agent._cfg.id/name/description`. The change migrated `_history` but not `_cfg`. Since the team touched this file in this change, leaving `_cfg` is intentional deferral — but per the project's no-tech-debt policy, this is debt.

2. **`ClearResponse` schema is dead code** — defined in `schemas.py` but never used by any endpoint. Minor, but clutter.

3. **Vacuous negative assertions in `test_agents_router.py`** — see W3 above. The tests give false confidence that `_history` is never accessed directly, but the assertions are not actually checking what they claim to check.

---

## summary

The implementation is architecturally sound: the CLI-via-REST migration correctly removes `AppContainer` from the CLI path, the new admin chat endpoints are well-structured, auth is properly applied to all new endpoints (fail-closed on unconfigured key), and the `_history` private access was successfully replaced with a public API. The main unresolved issue is that `_cfg` remains as a private access in `agents.py` — the team flagged this themselves but did not address it in this change, which conflicts with the project's no-tech-debt rule. Two test correctness issues (vacuous MagicMock assertions, untested CLI output paths) reduce confidence in the coverage numbers. No hexagonal violations in new code, no async httpx in the sync path, no resource leaks found.

---

## judge_id
A

## skill_resolution
injected
