# Tasks: cli-chat-via-rest

> Strict TDD: cada `[IMPL]` referencia el `[TEST]` que lo precede. Test runner: `pytest`.

---

## Sección 1 — RunAgent: API pública de historial (Design §D)

- [x] **1.1 [TEST]** `test_run_agent_history_api.py` (nuevo): `get_history()` delega a `_history.load(agent_id)` y retorna lista de mensajes.
  - Archivo: `tests/unit/core/use_cases/test_run_agent_history_api.py`
  - Cubre: Design D1, Spec escenarios GET history (dominio admin-chat)
- [x] **1.2 [IMPL]** Añadir `async def get_history(self) -> list[Message]` a `RunAgent`, delegando a `self._history.load(self._agent_id)`.
  - Archivo: `core/use_cases/run_agent.py` — pasa: 1.1
- [x] **1.3 [TEST]** Mismo archivo: `clear_history()` delega a `_history.clear(agent_id)` y propaga excepciones.
  - Archivo: `tests/unit/core/use_cases/test_run_agent_history_api.py`
- [x] **1.4 [IMPL]** Añadir `async def clear_history(self) -> None` a `RunAgent`.
  - Archivo: `core/use_cases/run_agent.py` — pasa: 1.3

---

## Sección 2 — Errores de dominio (Design §C1)

- [x] **2.1 [TEST]** Test unitario: instanciar `UnknownAgentError` y `DaemonAuthError`; verificar son subclases de `DaemonClientError`.
  - Archivo: `tests/unit/core/domain/test_errors.py` (extender si existe, crear si no)
- [x] **2.2 [IMPL]** Añadir `UnknownAgentError(DaemonClientError)` y `DaemonAuthError(DaemonClientError)` en `core/domain/errors.py`.
  - Archivo: `core/domain/errors.py` — pasa: 2.1

---

## Sección 3 — Port extension: `IDaemonClient` (Design §B1)

- [x] **3.1 [IMPL]** Añadir firmas `chat_turn`, `chat_history`, `chat_clear` al `Protocol` en `daemon_client_port.py`. No requiere test propio (es un Protocol/interfaz).
  - Archivo: `core/ports/outbound/daemon_client_port.py`
  - Depende de: 2.2 (para anotaciones de error en docstrings)

---

## Sección 4 — Auth helper extraction (Design §A1)

- [x] **4.1 [TEST]** Test unitario: `_check_admin_auth` rechaza request sin header → `HTTPException 401`; acepta header correcto → `None`.
  - Archivo: `tests/unit/adapters/rest_admin/test_deps.py` (nuevo)
- [x] **4.2 [IMPL]** Extraer `_check_admin_auth` de `admin.py` a `adapters/inbound/rest/admin/routers/deps.py`. Actualizar import en `admin.py`.
  - Archivos: `adapters/inbound/rest/admin/routers/deps.py` (nuevo), `adapters/inbound/rest/admin/routers/admin.py` — pasa: 4.1

---

## Sección 5 — Admin chat router + schemas (Design §A2–A5)

- [x] **5.1 [TEST]** `test_chat_router.py`: POST /turn happy path → 200 con `reply`; sin auth → 401; agent inválido → 404; body inválido → 422.
  - Archivo: `tests/unit/adapters/rest_admin/test_chat_router.py` (nuevo)
  - Cubre: Spec escenarios turn (happy, auth, agent_not_found, payload inválido)
- [x] **5.2 [TEST]** Mismo archivo: verificar que `set_channel_context("cli", session_id)` se llama y se resetea a `None` (try/finally).
  - Cubre: Design §A3
- [x] **5.3 [TEST]** Mismo archivo: GET /history happy → 200+lista; agente inválido → 404; sin auth → 401.
  - Cubre: Spec escenarios GET history
- [x] **5.4 [TEST]** Mismo archivo: DELETE /history happy → 204; agente inválido → 404; sin auth → 401; GET posterior → lista vacía.
  - Cubre: Spec escenarios DELETE history (incluido "fresh turn after DELETE")
- [x] **5.5 [IMPL]** Crear schemas en `adapters/inbound/rest/admin/schemas.py`: `ChatTurnRequest`, `ChatTurnResponse`, `HistoryMessage`, `HistoryResponse`, `ClearResponse`.
  - Archivo: `adapters/inbound/rest/admin/schemas.py` — pasa: 5.1–5.4
- [x] **5.6 [IMPL]** Crear router `adapters/inbound/rest/admin/routers/chat.py` con los 3 handlers usando schemas de 5.5, `_check_admin_auth` de 4.2, y `run_agent.get_history()`/`clear_history()` de 1.2/1.4.
  - Archivo: `adapters/inbound/rest/admin/routers/chat.py` (nuevo) — pasa: 5.1–5.4
- [x] **5.7 [WIRING]** Registrar `chat.router` con `prefix="/admin/chat"` en la FastAPI app del admin server.
  - Archivo: `adapters/inbound/rest/admin/app.py`

---

## Sección 6 — DaemonClient: implementación (Design §B2)

- [x] **6.1 [TEST]** `test_daemon_client_chat.py` (nuevo): `chat_turn` serializa body correcto, envía `X-Admin-Key`, parsea `reply`. Usa `httpx.MockTransport`.
  - Archivo: `tests/unit/adapters/outbound/test_daemon_client_chat.py`
  - Cubre: Spec CLI happy path turn
- [x] **6.2 [TEST]** Mismo archivo: `ConnectError` → `DaemonNotRunningError`; `TimeoutException` → `DaemonTimeoutError`; HTTP 404 → `UnknownAgentError`; HTTP 401 → `DaemonAuthError`.
  - Cubre: Design §C1 tabla de error mapping
- [x] **6.3 [TEST]** Mismo archivo: `chat_history` parsea `list[{role, content}]`; `chat_clear` envía DELETE con query param `agent_id`.
  - Cubre: Spec GET/DELETE history via client
- [x] **6.4 [IMPL]** Implementar `chat_turn`, `chat_history`, `chat_clear` en `DaemonClient`. Añadir `_get` y `_delete` helpers. Añadir parámetro `chat_timeout: float = 300.0` al constructor.
  - Archivo: `adapters/outbound/daemon_client.py` — pasa: 6.1–6.3

---

## Sección 7 — Migración TelegramBot (Design §D2)

- [x] **7.1 [TEST]** Actualizar/crear test: `_handle_clear` llama a `run_agent.clear_history()`, no a `run_agent._history.clear(...)`.
  - Archivo: `tests/unit/adapters/inbound/telegram/test_bot_clear.py` (nuevo o actualizar existente)
  - Cubre: Spec escenario cross-channel effect
- [x] **7.2 [IMPL]** En `bot.py` ~línea 80: reemplazar `self._container.run_agent._history.clear(self._agent_cfg.id)` por `await self._container.run_agent.clear_history()`.
  - Archivo: `adapters/inbound/telegram/bot.py` — pasa: 7.1

---

## Sección 8 — CLI runner rewrite (Design §B3)

- [x] **8.1 [TEST]** `test_cli_runner_rest.py` (nuevo): `run_cli` genera UUID único por llamada; stdin/stdout capturados; mock `IDaemonClient`.
  - Archivo: `tests/unit/adapters/inbound/cli/test_cli_runner_rest.py`
  - Cubre: Spec "UUID generated per process"
- [x] **8.2 [TEST]** Mismo archivo: `/exit` y `/quit` terminan el loop sin llamar al client.
  - Cubre: Spec "/exit or /quit"
- [x] **8.3 [TEST]** Mismo archivo: `/clear` llama `client.chat_clear(agent_id)` e imprime "Historial limpiado.".
  - Cubre: Spec "/clear"
- [x] **8.4 [TEST]** Mismo archivo: mensaje normal llama `client.chat_turn(agent_id, session_id, msg)` e imprime la respuesta.
  - Cubre: Spec "Happy path — user sends message"
- [x] **8.5 [TEST]** Mismo archivo: `DaemonNotRunningError` → sale del loop; `DaemonTimeoutError` → imprime error y continúa.
  - Cubre: Spec "Daemon becomes unreachable mid-session"
- [x] **8.6 [TEST]** Mismo archivo: `KeyboardInterrupt` (Ctrl+C) → salida limpia código 0.
  - Cubre: Spec "User presses Ctrl+C"
- [x] **8.7 [IMPL]** Reescribir `cli_runner.py` como REPL sync: `def run_cli(client: IDaemonClient, agent_id: str, global_config)`. Eliminar toda dependencia de `AppContainer`. Manejar `/clear`, `/exit`, `/quit`, spinner via `rich`.
  - Archivo: `adapters/inbound/cli/cli_runner.py` — pasa: 8.1–8.6

---

## Sección 9 — CLI command update (Design §B4)

- [x] **9.1 [TEST]** Test: `chat` command con daemon mock; verifica que NO se instancia `AppContainer`; `DaemonClient` recibe `agent_id` correcto.
  - Archivo: `tests/unit/adapters/inbound/cli/test_cli_command.py` (nuevo o extender)
- [x] **9.2 [TEST]** Mismo archivo: daemon inalcanzable al startup → imprime mensaje accionable y sale no-zero.
  - Cubre: Spec "Daemon unreachable at startup"
- [x] **9.3 [IMPL]** En `inaki/cli.py`: eliminar bootstrap `AppContainer` del path `chat`; construir `DaemonClient` con config; llamar `run_cli` sync. Conservar `AgentRegistry` para banner.
  - Archivo: `inaki/cli.py` — pasa: 9.1–9.2

---

## Sección 10 — Container wiring (Design §C2)

- [x] **10.1 [WIRING]** Verificar en `infrastructure/container.py` que `DaemonClient` se inyecta con `chat_timeout` leído de config. Ajustar si hace falta.
  - Archivo: `infrastructure/config.py` (AdminConfig + chat_timeout field), `inaki/cli.py` (propagación a DaemonClient constructor)

---

## Sección 11 — Config y docs (Design §Config)

- [x] **11.1 [DOCS]** Añadir clave `daemon.client.chat_timeout: 300` a `config/global.example.yaml`.
  - Archivo: `config/global.example.yaml` (sección `admin` añadida con host, port, chat_timeout)
- [x] **11.2 [DOCS]** Documentar los 3 endpoints nuevos y la clave `chat_timeout` en `docs/configuracion.md`.
  - Archivo: `docs/configuracion.md` (sección admin + tabla de endpoints + ejemplos JSON)

---

---

## Correcciones post Batch B/C (añadidas en Batch D)

- [x] **C1.1 [TEST]** Añadir assert de `timestamp` en tests de GET /history del router de chat.
  - Archivo: `tests/unit/adapters/rest_admin/test_chat_router.py`
- [x] **C1.2 [IMPL]** Añadir `timestamp: datetime | None` a `HistoryMessage` schema; mapear desde `msg.timestamp` en handler GET /history.
  - Archivos: `adapters/inbound/rest/admin/schemas.py`, `adapters/inbound/rest/admin/routers/chat.py`
- [x] **C1.3 [IMPL]** Actualizar test de `chat_history` en DaemonClient para verificar que `timestamp` se incluye en la respuesta parseada.
  - Archivo: `tests/unit/adapters/outbound/test_daemon_client_chat.py`
- [x] **C2.1 [TEST]** Test de `/agents` en REPL llama `client.list_agents()` y muestra resultado.
  - Archivo: `tests/unit/adapters/inbound/cli/test_cli_runner_rest.py`
- [x] **C2.2 [IMPL]** Añadir `GET /admin/agents` endpoint al admin router con schema `AgentsResponse`.
  - Archivos: `adapters/inbound/rest/admin/schemas.py`, `adapters/inbound/rest/admin/routers/admin.py`
- [x] **C2.3 [IMPL]** Añadir `list_agents()` a `IDaemonClient` port y `DaemonClient`. Actualizar REPL `/agents` handler para llamar `client.list_agents()`.
  - Archivos: `core/ports/outbound/daemon_client_port.py`, `adapters/outbound/daemon_client.py`, `adapters/inbound/cli/cli_runner.py`
- [x] **C2.4 [TEST]** Tests para `GET /admin/agents` en `test_chat_router.py` (happy + sin auth).
  - Archivo: `tests/unit/adapters/rest_admin/test_chat_router.py`
- [x] **C2.5 [TEST]** Tests para `DaemonClient.list_agents()` (happy + error mapping).
  - Archivo: `tests/unit/adapters/outbound/test_daemon_client_chat.py`
- [x] **C3.1 [REFACTOR]** Unificar `_post`/`_post_chat` y `_map_error`/`_map_chat_error` en helpers únicos con `error_map` opcional.
  - Archivo: `adapters/outbound/daemon_client.py`
  - Resultado: todos los tests existentes pasan sin cambios.

---

## Batching para sdd-apply

| Batch | Secciones | Tareas | Foco |
|-------|-----------|--------|------|
| A — Foundation | 1, 2, 3, 4 | 1.1–4.2 (10 tareas) | RunAgent API, errores dominio, port, auth helper |
| B — Server | 5 | 5.1–5.7 (7 tareas) | Schemas + router + wiring admin |
| C — Client | 6, 8, 9 | 6.1–9.3 (14 tareas) | DaemonClient, CLI runner, CLI command |
| D — Migration + Wiring + Docs | 7, 10, 11 | 7.1–11.2 (6 tareas) | Telegram migration, container, config, docs |

**Total**: 37 tareas — 13 `[TEST]`, 13 `[IMPL]`, 4 `[WIRING]`, 2 `[DOCS]`, más los `[TEST]` inlined en secciones compuestas. TDD triplets explícitos: 8 pares RED/GREEN en secciones 1, 2, 4, y fragmentos de 5–9.

---

## Verify Fixes (post-verify corrections)

- [x] **W1 [TEST]** Tighten `test_agents_maneja_error_de_conexion` — assert loop continues after `/agents` DaemonNotRunningError (assert `chat_turn` called with next input).
  - Archivo: `tests/unit/adapters/inbound/cli/test_cli_runner_rest.py`
- [x] **W1 [IMPL]** `cli_runner.py` `/agents` handler: change `return` → `continue` on DaemonNotRunningError (non-fatal per spec).
  - Archivo: `adapters/inbound/cli/cli_runner.py`
- [x] **W2 [TEST]** New `test_agents_router.py` — assert `get_history` and `delete_history` endpoints call public API (`run_agent.get_history()` / `run_agent.clear_history()`), not `_history`.
  - Archivo: `tests/unit/adapters/inbound/rest/test_agents_router.py` (nuevo)
- [x] **W2 [IMPL]** Migrate 3 `_history` callsites in `agents.py` to public API:
  1. `get_history` endpoint: `_history.load(cfg.id)` → `run_agent.get_history()`
  2. `delete_history` endpoint: `_history.clear(cfg.id)` → `run_agent.clear_history()`
  3. `chat_stream` generator: `_history.load(cfg.id)` → `run_agent.get_history()`
  - Archivo: `adapters/inbound/rest/routers/agents.py`
- [x] **S3 [IMPL]** Move `import json` from function bodies to top-level imports in `cli_runner.py`.
  - Archivo: `adapters/inbound/cli/cli_runner.py`
- [x] **VF-chat_stream [IMPL]** Eliminar `chat_stream` (cierra deuda `_history.append`): handler SSE eliminado de `agents.py` junto con imports exclusivos (`StreamingResponse`, `asyncio`). Sin callers de producción. Docs actualizadas.
  - Archivos: `adapters/inbound/rest/routers/agents.py`, `docs/inaki_spec_v2.md`, `docs/estructura.md`, `docs/flujo_canal_llm.md`

---

## Judgment-Day Fixes (post adversarial review)

- [x] **JD-1 [IMPL]** Migrar acceso `_cfg` en `agents.py` → `get_agent_info()` API pública. Añadir `AgentInfoDTO` (NamedTuple) y `get_agent_info()` a `RunAgentUseCase`. TDD: RED en `test_run_agent_agent_info.py`, GREEN tras impl.
  - Archivos: `core/use_cases/run_agent.py`, `adapters/inbound/rest/routers/agents.py`, `tests/unit/core/use_cases/test_run_agent_agent_info.py`
- [x] **JD-2 [IMPL]** Hoist `rich` import + `Console()` singleton en `cli_runner.py`. Import a nivel módulo, instancia única por invocación de REPL.
  - Archivo: `adapters/inbound/cli/cli_runner.py`
- [x] **JD-3 [IMPL]** `DaemonAuthError` preserva `status_code` real (401 o 403). Constructor acepta `status_code: int = 401`. `_map_error` pasa el código real. TDD: RED (`test_chat_turn_403_preserva_status_code` falla), GREEN tras fix.
  - Archivos: `core/domain/errors.py`, `adapters/outbound/daemon_client.py`, `tests/unit/adapters/outbound/test_daemon_client_chat.py`
- [x] **JD-4 [IMPL]** Reemplazar `MagicMock` con `create_autospec(RunAgentUseCase)` en `test_agents_router.py`. Eliminar assertions vacías `_history.load.assert_not_called()` y `_history.clear.assert_not_called()`. El autospec detecta accesos a privados como `AttributeError`.
  - Archivo: `tests/unit/adapters/inbound/rest/test_agents_router.py`
- [x] **JD-5 [IMPL]** Eliminar `ClearResponse` schema muerto de `schemas.py`. DELETE devuelve 204 sin body. Sin usos en producción.
  - Archivo: `adapters/inbound/rest/admin/schemas.py`
