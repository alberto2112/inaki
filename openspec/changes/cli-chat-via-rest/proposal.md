# Proposal: cli-chat-via-rest

**Change**: `cli-chat-via-rest`
**Project**: `inaki`
**Branch**: `feature/cli-chat-via-rest`
**Strict TDD**: ACTIVE (downstream `sdd-apply` y `sdd-verify` deben seguir strict-tdd.md)
**Artifact store**: hybrid (engram + openspec file)

---

## Intent

Migrar `inaki chat` para que hable al daemon via REST turn-based en vez de bootstrapping un `AppContainer` local. Hoy el CLI carga embeddings ONNX, abre SQLite de memoria, descubre extensiones y registra tools en cada invocación — duplicando el proceso del daemon en el mismo host (Raspberry Pi 5, 4GB RAM). El objetivo es que el CLI sea un **cliente HTTP delgado** contra el admin server (puerto 6497), reutilizando `run_agent.execute()` del daemon para preservar la tool loop intacta y mantener paridad con la UX actual.

---

## Approach (Option C — turn-based request/response)

- El admin server expone tres endpoints nuevos bajo `/admin/chat/*`, todos autenticados con `X-Admin-Key`, enrutando por `agent_id` al `AgentContainer` correcto del `AppContainer`.
- Cada turno de chat es una request `POST` síncrona: CLI envía mensaje, daemon llama a `run_agent.execute()` (tool loop completa), responde con el mensaje final del asistente en JSON. No hay SSE, no hay streaming de tokens.
- `DaemonClient` (adapter existente, `httpx` sync) gana métodos nuevos para chat, history y clear.
- `cli_runner.py` deja de instanciar `AppContainer` en modo chat: todo pasa por `DaemonClientPort`.
- La UX permanece idéntica a hoy: el usuario escribe, ve un spinner/cursor quieto, aparece la respuesta completa. Es **paridad con el estado actual** (hoy el CLI tampoco hace streaming).

---

## Scope (in)

- Nuevos endpoints en admin server: `POST /admin/chat/turn`, `GET /admin/chat/history`, `DELETE /admin/chat/history`.
- Nuevo método port en `DaemonClientPort`: `send_turn`, `get_history`, `clear_history`.
- Implementación en `adapters/outbound/daemon_client.py` usando `httpx` sync (patrón `_post`/`_get` existente).
- Refactor de `cli_runner.py` para usar el port en lugar de `AppContainer`.
- Generación de `session_id` (UUID client-side por proceso) en el arranque del CLI, enviado en body JSON de cada turno.
- Eliminación del path de bootstrap local de `AppContainer` en el flujo `chat` (hard cutover).
- Tests unitarios e integración siguiendo Strict TDD: contract tests de los endpoints admin, tests de `DaemonClient` con `httpx.MockTransport`, test del `cli_runner` con `DaemonClientPort` mockeado.

## Scope (out)

- **SSE / streaming de tokens**: descartado por explore, no es regresión.
- **Auth distinto de `X-Admin-Key`**: no se introduce OAuth, JWT, ni multi-user.
- **Historial segmentado por `session_id` / `user_id`**: sigue siendo por `agent_id` plano.
- **Pagination / filtering en `GET /admin/chat/history`**: se devuelve `load()` completo como hoy.
- **Resume de sesión tras restart del CLI**: UUID nuevo en cada proceso.
- **Multi-agente cambiante mid-session** (`/agent dev`): `--agent` se fija al arranque.
- **Unificar auth con per-agent REST (`X-API-Key`)**.
- **Migrar `/consolidate`, `/inspect`** — ya funcionan via daemon; no se tocan.
- **Puerto per-agent REST (6498+)** como transporte del chat CLI.

---

## Endpoint surface (shape only; schemas finales los define `sdd-spec` / `sdd-design`)

| Verb   | Path                      | Propósito |
|--------|---------------------------|-----------|
| POST   | `/admin/chat/turn`        | Envía un mensaje al agente y devuelve la respuesta completa del asistente. Body incluye `agent_id`, `session_id`, `message`. |
| GET    | `/admin/chat/history`     | Devuelve el historial activo (`load()`, no `load_full()`) del agente. Query param `agent_id`. |
| DELETE | `/admin/chat/history`     | Limpia todo el historial del agente (paridad con Telegram `/clear`). Query param `agent_id`. |

Todos con header `X-Admin-Key`, fail-closed siguiendo `_check_admin_auth` existente.

---

## Key decisions locked

1. **Transport** → **Turn-based request/response (Option C)**. Reutiliza `run_agent.execute()` intacto, paridad con UX actual, cero refactor del use case, cero riesgo sobre la tool loop.
2. **Endpoint surface** → tres endpoints bajo `/admin/chat/*` en el admin server (puerto 6497). `GET /history` está **in-scope v1** porque el comando `/history` del CLI existe hoy y debe seguir funcionando.
3. **Session identification** → **UUID client-side en body JSON** como campo `session_id`. Rationale: más explícito que header, visible en logs/debug, consistente con `agent_id` que también va en body. El servidor lo mapea a `ChannelContext(channel_type="cli", user_id=session_id)`.
4. **Agent selection** → **`agent_id` por request (stateless)**. Rationale: consistente con cómo `inspect` y `consolidate` ya funcionan en el admin server; sin estado nuevo; `ChannelContext` análogo ya es efímero por request.
5. **`/clear` semantics** → **paridad estricta con Telegram**: borra TODA la historia del agente (por `agent_id`, sin segmentar por `session_id`). El explore confirmó que el historial es plano. Segmentar por sesión queda out-of-scope.
6. **Backwards compatibility** → **hard cutover**. Rationale: el bootstrap local ya se removió parcialmente (commit `c40de00` removió el flag standalone); mantener dos paths duplica superficie y contradice el objetivo (CLI delgado). El daemon es ahora un prerequisito duro para `inaki chat`. Error claro si el daemon está caído.
7. **Out of scope explícito**: SSE/streaming, multi-user CLI, history pagination, auth adicional.

---

## Alternatives considered (rejected)

- **Option A — SSE streaming**: rechazada. Requiere refactor de `run_agent.execute()` para emitir tokens incrementalmente, o bypass de tool loop (como hace hoy `/chat/stream` per-agent REST). Riesgo alto sin beneficio UX real (hoy tampoco stremeamos).
- **Option B — WebSocket**: rechazada. `httpx` no soporta WebSockets, romperíamos el estándar del proyecto (httpx sync). Complejidad desproporcionada para un CLI single-user turn-based.

---

## Risks & mitigations

| Riesgo | Mitigación |
|--------|------------|
| Respuestas largas (agente con tool loop profunda) se sienten "colgadas" sin feedback. | Spinner/indicador en el CLI mientras dura el turn. Configurar timeout `httpx` generoso (ej. 300s) derivado de `tools.tool_call_max_iterations`. |
| Daemon caído → `inaki chat` queda inusable (consecuencia del hard cutover). | Mensaje de error claro del `DaemonClient` ("daemon no responde en 127.0.0.1:6497, arrancá con `inaki daemon`"). Documentar en README. |
| Timeout HTTP corta un turno a mitad de generación. | Timeout configurable via `global.yaml` (daemon.client.timeout). Default alto. |
| `session_id` expuesto en body JSON aparece en logs. | Es un UUID opaco sin PII; aceptable. |
| Routing `app_container.agents[agent_id]` con `agent_id` inválido. | Retornar `404` con mensaje claro; test de contract. |
| Concurrencia: dos CLIs en paralelo sobre mismo agente interleavan historial. | Ya sucede hoy (historial por `agent_id`), no es regresión. Documentar. |

---

## Migration stance — recomendación: **hard cutover**

El commit `c40de00` ya removió el flag `--standalone` y el bootstrap legacy. Mantener un segundo path "bootstrap local" via feature flag duplicaría superficie y contradice el objetivo. El daemon es un proceso liviano y arrancarlo es el patrón esperado en el target platform (systemd en Raspi 5). El error cuando el daemon está caído debe ser accionable y mencionar `inaki daemon`.

---

## Impact surface (archivos que se tocan — lista, sin diffs)

**Core (port nuevo / extendido):**
- `core/ports/outbound/daemon_client_port.py` — agregar métodos `send_turn`, `get_history`, `clear_history`.

**Adapters inbound (admin server):**
- `adapters/inbound/rest/admin/routers/admin.py` — tres endpoints nuevos `/admin/chat/turn`, `/admin/chat/history` (GET y DELETE).
- Posiblemente nuevo módulo `adapters/inbound/rest/admin/routers/chat.py` si se quiere separar del router admin general (decide `sdd-design`).
- DTOs Pydantic para request/response (ubicación define `sdd-design`).

**Adapters inbound (CLI):**
- `adapters/inbound/cli/cli_runner.py` — reemplazar bootstrap de `AppContainer` por consumo de `DaemonClientPort`; generar UUID session_id al arrancar; cablear `/clear` y `/history` a los endpoints REST.

**Adapters outbound (client):**
- `adapters/outbound/daemon_client.py` — implementar `send_turn`, `get_history`, `clear_history` reutilizando `_post`/`_get`/`_delete` o análogos.

**Infrastructure (wiring):**
- `infrastructure/container.py` — confirmar que `DaemonClientPort` está inyectado en el CLI runner; ajustar si es necesario.

**Tests:**
- `tests/unit/adapters/inbound/rest/admin/` — tests de los tres endpoints con `TestClient`.
- `tests/unit/adapters/outbound/test_daemon_client.py` — tests de los métodos nuevos con `httpx.MockTransport`.
- `tests/unit/adapters/inbound/cli/test_cli_runner.py` — tests del flujo chat con `DaemonClientPort` mockeado.
- `tests/integration/` — test end-to-end: CLI ↔ daemon real con `:memory:` DB.

**Docs:**
- `docs/configuracion.md` y/o `README.md` — actualizar el flujo `inaki chat` (daemon requerido).

---

## Notas para `sdd-spec` / `sdd-design`

- `sdd-spec` debe producir scenarios Given/When/Then para: turn exitoso, turn con agent_id inválido, turn con auth inválido, clear, history vacío, history con N mensajes, daemon caído (client-side).
- `sdd-design` decide: schemas Pydantic exactos, ubicación de DTOs, si los endpoints van en un router nuevo `chat.py` o en `admin.py`, timeout default, formato de errores.
- Ambas fases pueden ir en paralelo (dependen solo del proposal).
