# Apply Progress — cli-chat-via-rest

## Status: ALL COMPLETE — 55 tasks (37 original + 9 corrections + 3 verify fixes + 1 VF-chat_stream + 5 Judgment-Day fixes)

**Last updated**: 2026-04-15
**Full suite**: 669 passed, 25 failed (all 25 pre-existing — 0 regressions)

---

## Batch A — Foundation (§1–4)

**Status**: COMPLETE (10/10 tasks)

### TDD Cycle Evidence

| Task | RED | GREEN | Notes |
|------|-----|-------|-------|
| 1.1 TEST — get_history | ✅ AttributeError | — | |
| 1.2 IMPL — get_history | — | ✅ | Added to RunAgentUseCase |
| 1.3 TEST — clear_history | ✅ same RED | — | |
| 1.4 IMPL — clear_history | — | ✅ | Added to RunAgentUseCase |
| 2.1 TEST — error classes | ✅ ImportError | — | |
| 2.2 IMPL — error classes | — | ✅ | UnknownAgentError + DaemonAuthError |
| 3.1 IMPL — port extension | — | ✅ | Protocol only |
| 4.1 TEST — check_admin_auth | ✅ ModuleNotFoundError | — | |
| 4.2 IMPL — deps.py extraction | — | ✅ | deps.py created |

---

## Batch B — Server (§5)

**Status**: COMPLETE (7/7 tasks)

### TDD Cycle Evidence

| Task | RED | GREEN | Notes |
|------|-----|-------|-------|
| 5.1-5.4 TEST — chat router | ✅ ImportError | — | 16 tests written first |
| 5.5 IMPL — schemas | — | ✅ | 5 Pydantic models |
| 5.6 IMPL — chat router | — | ✅ | 3 handlers |
| 5.7 WIRING — register router | — | ✅ | /admin/chat prefix |

---

## Batch C — Client (§6, 8, 9)

**Status**: COMPLETE (14/14 tasks)

### TDD Cycle Evidence

| Task | RED | GREEN | Notes |
|------|-----|-------|-------|
| 6.1-6.3 TEST — DaemonClient chat | ✅ TypeError | — | 23 tests written first |
| 6.4 IMPL — DaemonClient | — | ✅ | chat_turn, chat_history, chat_clear |
| 8.1-8.6 TEST — CLI runner | ✅ various | — | 13 tests |
| 8.7 IMPL — cli_runner.py rewrite | — | ✅ | Sync REPL |
| 9.1-9.2 TEST — CLI command | ✅ | — | |
| 9.3 IMPL — cli.py update | — | ✅ | Dropped bootstrap |

---

## Batch D — Migration + Wiring + Docs (§7, 10, 11)

**Status**: COMPLETE (6/6 tasks)

### TDD Cycle Evidence

| Task | RED | GREEN | Notes |
|------|-----|-------|-------|
| 7.1 TEST — bot._cmd_clear migration | ✅ awaited 0 times | — | test_bot_clear.py |
| 7.2 IMPL — bot.py | — | ✅ | `clear_history()` API pública |
| 10.1 WIRING — chat_timeout | — | ✅ | AdminConfig.chat_timeout + cli.py propagation |
| 11.1 DOCS — global.example.yaml | — | ✅ | `admin:` section added |
| 11.2 DOCS — configuracion.md | — | ✅ | 4 endpoints + JSON examples |

---

## Corrections (post Batch B/C review)

**Status**: COMPLETE (9/9 tasks)

### Correction 1 — timestamp in HistoryMessage

| Task | RED | GREEN | Notes |
|------|-----|-------|-------|
| C1.1 TEST — assert timestamp | ✅ KeyError | — | test_chat_router.py updated |
| C1.2 IMPL — schema + handler | — | ✅ | HistoryMessage.timestamp: datetime | None |
| C1.3 — DaemonClient test | ✅ → GREEN immediately | ✅ | Client already passthrough |

### Correction 2 — /agents in REPL

| Task | RED | GREEN | Notes |
|------|-----|-------|-------|
| C2.1 TEST — /agents calls list_agents() | ✅ called 0 times | — | test_cli_runner_rest.py |
| C2.2 IMPL — GET /admin/agents | — | ✅ | AgentsResponse + admin router |
| C2.3 IMPL — list_agents() everywhere | — | ✅ | port + DaemonClient + REPL |
| C2.4 TEST — endpoint tests | ✅ → GREEN | ✅ | test_chat_router.py 2 new tests |
| C2.5 TEST — DaemonClient.list_agents | — | ✅ | 3 new tests |

### Correction 3 — Unified _post/_map_error (REFACTOR)

No new test. All 26 DaemonClient tests still pass.
- Removed `_post_chat` and `_map_chat_error`
- Unified into `_post(error_map=None)`, `_get(error_map=None)`, `_delete(error_map=None)`, `_map_error(error_map=None)`
- `_CHAT_ERROR_MAP = {404: UnknownAgentError, 401/403: DaemonAuthError}` class attribute
- Legacy callers use `_post` without `error_map` (DaemonClientError generic)
- Chat callers pass `error_map=self._CHAT_ERROR_MAP`

---

## Files Touched (Batch D + Corrections)

### Production
- `adapters/inbound/telegram/bot.py` — `_cmd_clear` → `clear_history()`
- `adapters/inbound/rest/admin/schemas.py` — `HistoryMessage.timestamp` + `AgentsResponse`
- `adapters/inbound/rest/admin/routers/chat.py` — timestamp mapped in GET /history handler
- `adapters/inbound/rest/admin/routers/admin.py` — `GET /admin/agents` endpoint
- `adapters/outbound/daemon_client.py` — unified helpers + `list_agents()`
- `adapters/inbound/cli/cli_runner.py` — `/agents` calls `client.list_agents()`
- `core/ports/outbound/daemon_client_port.py` — `list_agents()` added to Protocol
- `infrastructure/config.py` — `AdminConfig.chat_timeout: float = 300.0`
- `inaki/cli.py` — propagates `admin.chat_timeout` to `DaemonClient`
- `config/global.example.yaml` — `admin:` section with `chat_timeout`
- `docs/configuracion.md` — admin endpoints documented

### Tests
- `tests/unit/adapters/inbound/telegram/__init__.py` (new)
- `tests/unit/adapters/inbound/telegram/test_bot_clear.py` (new, 3 tests)
- `tests/unit/adapters/rest_admin/test_chat_router.py` (updated — timestamp + /agents)
- `tests/unit/adapters/outbound/test_daemon_client_chat.py` (updated — timestamp + list_agents)
- `tests/unit/adapters/inbound/cli/test_cli_runner_rest.py` (updated — /agents)

### Specs/Tasks
- `openspec/changes/cli-chat-via-rest/tasks.md` — all tasks [x] including corrections
- `openspec/changes/cli-chat-via-rest/specs/admin-chat/spec.md` — timestamp schema + /agents
- `openspec/changes/cli-chat-via-rest/specs/cli-chat-client/spec.md` — /agents requirement

---

## Verify Fixes (W1, W2, S3) — COMPLETE

### W1 — /agents non-fatal DaemonNotRunningError

**Problem**: `cli_runner.py` `/agents` handler was calling `return` on `DaemonNotRunningError`, exiting the REPL. Spec says non-fatal.

**TDD**:
- RED: tightened `test_agents_maneja_error_de_conexion` — added assertion that `chat_turn` is called on subsequent input after `/agents` error
- GREEN: changed `return` → `continue`, removed "Saliendo." from the error message for /agents

### W2 — Migrate agents.py to public history API

**Problem**: `adapters/inbound/rest/routers/agents.py` accessed `_history` directly in 3 places.

**3 callsites migrated**:
1. `get_history` endpoint: `_history.load(cfg.id)` → `run_agent.get_history()`
2. `delete_history` endpoint: `_history.clear(cfg.id)` → `run_agent.clear_history()`
3. `chat_stream` generator: `_history.load(cfg.id)` → `run_agent.get_history()`

**Note**: `_history.append` in `chat_stream` has no public API — left as-is, flagged for follow-up.

**TDD**:
- RED: new test file `test_agents_router.py` — TypeError on await MagicMock
- GREEN: 4 tests pass after migration

### S3 — import json top-level

Moved two `import json` from function bodies to top-level in `cli_runner.py`. No test changes.

### Verify Fixes — Files Touched

Production:
- `adapters/inbound/cli/cli_runner.py` — W1 (return→continue) + S3 (top-level import json)
- `adapters/inbound/rest/routers/agents.py` — W2 (3 callsites → public API)

Tests:
- `tests/unit/adapters/inbound/cli/test_cli_runner_rest.py` — W1 test tightened (15 tests)
- `tests/unit/adapters/inbound/rest/__init__.py` (new)
- `tests/unit/adapters/inbound/rest/test_agents_router.py` (new, 4 tests)

### Full Suite
- 690 passed, 25 failed (25 pre-existing — 0 regressions)

---

---

## Judgment-Day Fixes (2026-04-15)

**Status**: COMPLETE (5/5)

### TDD Cycle Evidence

| Fix | RED | GREEN | Notes |
|-----|-----|-------|-------|
| JD-1 `get_agent_info()` | ✅ AttributeError en test_run_agent_agent_info.py | ✅ | AgentInfoDTO + get_agent_info() añadidos a RunAgentUseCase |
| JD-2 rich import hoist | — mecánico — | ✅ | Console() singleton por REPL, import a nivel módulo |
| JD-3 DaemonAuthError status_code | ✅ `assert 403 == 401` falla | ✅ | Constructor acepta status_code; _map_error pasa código real |
| JD-4 create_autospec | ✅ assertions vacías detectadas | ✅ | _history accesses ahora levantan AttributeError |
| JD-5 ClearResponse eliminado | — mecánico — | ✅ | Sin usos en producción; DELETE retorna 204 sin body |

### Archivos modificados

Production:
- `core/use_cases/run_agent.py` — AgentInfoDTO (NamedTuple) + get_agent_info() añadidos
- `adapters/inbound/rest/routers/agents.py` — 3 callsites _cfg → get_agent_info()
- `adapters/inbound/cli/cli_runner.py` — rich import hoisted, Console() singleton
- `core/domain/errors.py` — DaemonAuthError acepta status_code param
- `adapters/outbound/daemon_client.py` — _map_error pasa status_code a DaemonAuthError
- `adapters/inbound/rest/admin/schemas.py` — ClearResponse eliminado

Tests:
- `tests/unit/core/use_cases/test_run_agent_agent_info.py` (nuevo, 4 tests)
- `tests/unit/adapters/outbound/test_daemon_client_chat.py` (2 nuevos tests JD-3)
- `tests/unit/adapters/inbound/rest/test_agents_router.py` (migrado a create_autospec)

---

## Verify Fixes — VF-chat_stream (eliminación endpoint SSE)

**Problema**: `chat_stream` en `agents.py` accedía a `_history.append()` directamente (atributo privado). Sin callers de producción. Deuda técnica residual.

**Acción**: Endpoint eliminado completamente. Sin tests existentes que borrar (cero tests para `/chat/stream`).

**Eliminado**:
- Handler `chat_stream` con su `event_generator` (45 líneas)
- Import `StreamingResponse` (exclusivo del handler)
- Import `asyncio` (exclusivo del handler)
- Docs: `POST /chat/stream` de `inaki_spec_v2.md`, sección "Streaming SSE" de `inaki_spec_v2.md`, referencia en `estructura.md`, sección completa en `flujo_canal_llm.md`

**TDD**: No hay tests a borrar. Confirmación: `grep chat_stream tests/` → sin resultados.

**Archivos modificados**:
- `adapters/inbound/rest/routers/agents.py`
- `docs/inaki_spec_v2.md`
- `docs/estructura.md`
- `docs/flujo_canal_llm.md`
- `openspec/changes/cli-chat-via-rest/tasks.md` (entrada VF-chat_stream)
