# Design: cli-chat-via-rest

**Change**: `cli-chat-via-rest`
**Project**: `inaki`
**Phase**: sdd-design
**Dependencies**: proposal (locked), spec (parallel)

---

## Summary

El CLI `inaki chat` se convierte en un cliente HTTP turn-based delgado contra el admin server del daemon (puerto 6497). Se añaden tres endpoints nuevos bajo `/admin/chat/*` (`POST /turn`, `GET /history`, `DELETE /history`) montados en un **router nuevo** `chat.py` separado de `admin.py` para mantener cohesión por dominio. El CLI genera un `session_id` UUID por proceso y lo envía en el body JSON; el servidor lo mapea a `ChannelContext(channel_type="cli", user_id=session_id)` alrededor del call a `run_agent.execute()`. El `DaemonClient` (httpx sync) gana tres métodos nuevos y el `cli_runner.py` se rescribe como REPL puro sobre `IDaemonClient`, eliminando toda dependencia de `AppContainer` en el path chat (hard cutover).

---

## Component Diagram

```
┌────────────────────┐        ┌────────────────────────┐
│  inaki cli.py      │        │  Usuario (terminal)    │
│  (Typer)           │◄──────►│                        │
└─────────┬──────────┘        └────────────────────────┘
          │ run_cli(client, agent_id, global_config)
          ▼
┌────────────────────────────────────────────────────────┐
│  adapters/inbound/cli/cli_runner.py  (REPL)            │
│   - genera session_id UUID                             │
│   - parsea /clear /history /exit /quit                 │
│   - delega turnos a IDaemonClient                      │
└─────────┬──────────────────────────────────────────────┘
          │ IDaemonClient (port)
          ▼
┌────────────────────────────────────────────────────────┐
│  adapters/outbound/daemon_client.py  (httpx sync)      │
│   chat_turn / chat_history / chat_clear                │
└─────────┬──────────────────────────────────────────────┘
          │ HTTP + X-Admin-Key
          ▼
┌────────────────────────────────────────────────────────┐
│  admin server (FastAPI, puerto 6497)                   │
│  routers/chat.py  ←── router NUEVO                     │
│   POST   /admin/chat/turn                              │
│   GET    /admin/chat/history                           │
│   DELETE /admin/chat/history                           │
└─────────┬──────────────────────────────────────────────┘
          │ app.state.app_container.agents[agent_id]
          ▼
┌────────────────────────────────────────────────────────┐
│  AgentContainer                                        │
│   - set_channel_context(cli:session_id)                │
│   - run_agent.execute(user_input) → str                │
│   - run_agent.get_history() → list[Message]  (PUBLIC)  │
│   - run_agent.clear_history() → None         (PUBLIC)  │
│   - set_channel_context(None)                          │
└────────────────────────────────────────────────────────┘
```

---

## A. Server-side design

### A1. File layout — router nuevo `chat.py`

**Decisión**: crear `adapters/inbound/rest/admin/routers/chat.py` y montarlo en el FastAPI app junto a `admin.py`.

**Rationale**:
- `admin.py` hoy tiene endpoints transversales del daemon (health, scheduler/reload, inspect, consolidate) — no es por agente.
- Los tres endpoints de chat son un dominio cohesivo distinto: giran sobre `agent_id` + historial + turno conversacional.
- Separar reduce el blast radius de futuras variaciones (streaming, multi-session) y facilita tests.
- Precedente: el per-agent REST ya tiene `agents.py` dedicado al chat; seguimos la misma convención.
- El `_check_admin_auth` se importa de `admin.py` (o se extrae a `adapters/inbound/rest/admin/deps.py` si queremos evitar import cruzado entre routers — decisión menor que queda para apply).

**Wiring**: en el módulo que construye la FastAPI app del admin (donde ya se hace `app.include_router(admin.router)`), añadir `app.include_router(chat.router, prefix="/admin/chat")`.

### A2. Pydantic schemas

Ubicación: `adapters/inbound/rest/admin/schemas.py` (extender el archivo existente — un solo lugar para schemas admin).

```
ChatTurnRequest:
    agent_id: str
    session_id: str         # UUID string, validado con min_length
    message: str            # no vacío

ChatTurnResponse:
    reply: str              # texto final del assistant
    agent_id: str           # echo
    session_id: str         # echo

HistoryMessage:
    role: str               # Role.value: "user" | "assistant" | "system" | "tool"
    content: str

HistoryResponse:
    agent_id: str
    messages: list[HistoryMessage]

ClearResponse:
    agent_id: str
    cleared: bool = True
```

Notas:
- `session_id` NO se persiste en el historial (el store es por `agent_id` plano). Solo vive en el `ChannelContext` durante el request.
- `HistoryMessage` es un DTO plano — no exponemos `ToolResult`, tool_calls ni metadata del mensaje interno (paridad con lo que hoy imprime `/history` en CLI: `role: content`).
- Usar `pydantic.BaseModel` (v2), con `model_config = ConfigDict(extra="forbid")` para detectar payloads malformados.

### A3. Handler flow — `POST /admin/chat/turn`

```
1. Depends(_check_admin_auth)  → valida X-Admin-Key (fail-closed).
2. Resolver agent_container:
     app_container = request.app.state.app_container
     if body.agent_id not in app_container.agents: raise 404
     agent_container = app_container.agents[body.agent_id]
3. Construir ChannelContext("cli", user_id=body.session_id).
4. try:
       agent_container.set_channel_context(ctx)
       reply = await agent_container.run_agent.execute(body.message)
   finally:
       agent_container.set_channel_context(None)
5. return ChatTurnResponse(reply=reply, agent_id=..., session_id=...)
```

**Async boundary**: FastAPI handler es `async def`; `run_agent.execute()` ES `async` → se awaitea directamente. No hay que cruzar boundary sync/async (el problema sync/async solo aparece en el CLIENT, no en el server).

**Set/unset channel context**: el `try/finally` replica el patrón del `cli_runner.py` actual. Es crítico para evitar leak de context entre requests concurrentes (dos clientes CLI distintos no deben compartir `user_id`).

**Concurrencia**: si dos requests de chat llegan al mismo `agent_id`, se serializan a nivel de `run_agent.execute()` (no cambia respecto a hoy con Telegram + CLI). El `set_channel_context` mutable sobre el container es un riesgo conocido — se acepta porque es paridad con el estado actual y out-of-scope refactorizarlo.

### A4. Handler flow — `GET /admin/chat/history`

Query param: `agent_id`.

```
1. auth
2. validar agent_id existe (404 si no)
3. history_messages = await agent_container.run_agent.get_history()
4. mapear a list[HistoryMessage] (role.value + content)
5. return HistoryResponse(agent_id=..., messages=[...])
```

Acceso al history: **vía el método público `get_history()` de `RunAgent`** (ver sección D). Devuelve historial activo, sin archivados/infused — consistente con el comando `/history` de hoy.

### A5. Handler flow — `DELETE /admin/chat/history`

Query param: `agent_id`.

```
1. auth
2. validar agent_id existe (404 si no)
3. await agent_container.run_agent.clear_history()
4. return ClearResponse(agent_id=..., cleared=True)
```

Paridad estricta con `/clear` de Telegram y el comando CLI actual. No duplicamos lógica — usa el mismo método público `clear_history()` definido en `RunAgent` (ver sección D), el mismo que se usa en `TelegramBot._handle_clear` tras la migración.

### A6. Timeouts (design default)

- Server-side: FastAPI/uvicorn no tiene timeout duro por request (eso se maneja en el client).
- Client-side: `/turn` es el endpoint potencialmente lento (tool loop con N iteraciones + streaming LLM). Default recomendado para `httpx` en `chat_turn`: **300s (5 min)**, configurable via `global.yaml` (clave `daemon.client.chat_timeout`).
- `/history` y `/clear` son rápidos: `_LONG_TIMEOUT = 30.0` existente es suficiente.

Los valores concretos y la clave de config los cristaliza `sdd-tasks` + `sdd-apply`.

---

## B. Client-side design

### B1. `IDaemonClient` port — nuevos métodos

Ubicación: `core/ports/outbound/daemon_client_port.py`. Añadir al `Protocol`:

```
def chat_turn(self, agent_id: str, session_id: str, message: str) -> str: ...
def chat_history(self, agent_id: str) -> list[dict[str, str]]: ...
def chat_clear(self, agent_id: str) -> None: ...
```

**Rationale de shapes**:
- `chat_turn` devuelve `str` (el reply) — el DTO de transporte no se filtra al port; es un adapter concern.
- `chat_history` devuelve `list[dict[str, str]]` con claves `role` / `content` — estructura mínima que el CLI necesita para imprimir. Mantiene el port desacoplado del HTTP schema y de `core/domain/value_objects/message.py` (el CLI no necesita reconstruir `Message`, solo imprimir).
- `chat_clear` retorna `None`; errores via excepciones del dominio (no bool silencioso — es una operación que el user invocó, debe fallar ruidosamente).

Nombres con prefijo `chat_` para evitar colisión con métodos futuros (`send_turn` genérico queda ambiguo).

### B2. `DaemonClient` implementation approach

Ubicación: `adapters/outbound/daemon_client.py`. Extender la clase existente.

- `chat_turn`: POST `/admin/chat/turn` con body `{agent_id, session_id, message}`, timeout **largo (300s default, parametrizable via constructor)**. Reusa `_post` pero necesita `timeout` explícito — o creamos un `_post_long(...)` helper si queremos dejarlo más explícito (decisión menor).
- `chat_history`: GET `/admin/chat/history?agent_id=X`. Necesita un `_get` helper nuevo (hoy no existe — todo el cliente usa POST). Devuelve `resp.json()["messages"]`.
- `chat_clear`: DELETE `/admin/chat/history?agent_id=X`. Necesita `_delete` helper nuevo.

**Error handling** (todos los métodos):
- `httpx.ConnectError` → `DaemonNotRunningError` (existente, mensaje claro "arrancá con `inaki daemon`").
- `httpx.TimeoutException` → `DaemonTimeoutError` (existente).
- HTTP 404 (agent_id inválido) → nuevo `UnknownAgentError(agent_id)` en `core/domain/errors.py`, subclase de `DaemonClientError`.
- HTTP 401/403 (auth) → nuevo `DaemonAuthError` en `core/domain/errors.py`, subclase de `DaemonClientError`.
- HTTP 5xx → `DaemonClientError` existente (ya cubre).

**Constructor change**: añadir parámetro opcional `chat_timeout: float = 300.0`. El timeout de chat se lee de config en `cli.py` (igual que `admin_base_url` y `auth_key`). No inventamos nuevas capas de config — extendemos `global.yaml` existente si hace falta.

**httpx sync, NUNCA async** (regla del proyecto).

### B3. CLI runner rewrite (`cli_runner.py`)

Cambio estructural: el REPL deja de ser `async def run_cli(app: AppContainer, agent_id)` y pasa a ser **sync** `def run_cli(client: IDaemonClient, agent_id: str, global_config)`. No hay razón para `asyncio.run(...)` — todas las operaciones del CLI son sync HTTP calls.

Esqueleto (pseudo, NO código):

```
def run_cli(client, agent_id, global_config):
    session_id = str(uuid.uuid4())
    print_banner(agent_id, global_config)  # usa global_config.agents metadata si disponible, o solo agent_id

    while True:
        user_input = input("tú > ").strip()
        if not user_input: continue

        if user_input in ("/exit", "/quit"): break
        if user_input == "/help": print(_HELP); continue

        if user_input == "/history":
            try: msgs = client.chat_history(agent_id)
            except DaemonError as e: print_error(e); continue
            render_history(msgs)
            continue

        if user_input == "/clear":
            try: client.chat_clear(agent_id)
            except DaemonError as e: print_error(e); continue
            print("Historial limpiado.")
            continue

        # /consolidate y /inspect ya usan client.consolidate / client.inspect hoy — se mantienen.
        if user_input == "/consolidate": ...
        if user_input.startswith("/inspect"): ...

        # turno de chat
        try:
            reply = client.chat_turn(agent_id, session_id, user_input)
            print(f"\niñaki > {reply}\n")
        except DaemonNotRunningError as e:
            print(f"{e}\nSaliendo.")  # fatal
            break
        except DaemonTimeoutError as e:
            print(f"{e}\nIntentá de nuevo.")  # transient, sigue REPL
            continue
        except DaemonClientError as e:
            print(f"Error: {e}")
            continue
```

**Banner**: sin acceso a `AgentContainer`, ya no conocemos `llm.provider/model`. Opciones:
- (a) leer `global_config.app.default_agent` y mostrar solo el agent_id + descripción desde `AgentRegistry` (registry sigue siendo liviano, carga YAML sin instanciar providers).
- (b) hacer un endpoint admin nuevo `GET /admin/agents/{id}` que devuelva metadatos.

**Decisión**: (a). El `AgentRegistry` NO es el problema que motiva este cambio — es ligero (solo parsea YAML). El `cli.py` ya lo carga. Pasamos `global_config` + `registry` al runner para el banner. Coste: runner recibe tres args (client, agent_id, registry/global_config). Aceptable.

**NO importar `AppContainer`, `RunAgentUseCase`, ni nada del daemon**. Solo `IDaemonClient`, `AgentRegistry`, y tipos de `core/domain/errors.py`.

**Elimino**: `print_inspect` queda donde está (se sigue usando); `list_agents` usa `registry.list_all()` — ya funciona sin AppContainer.

### B4. `inaki/cli.py` changes

- `_invoke_default_chat` y `chat`: eliminar las líneas que llaman a `_bootstrap(config_dir, agents_dir)` **solo para el chat**. Conservar el bootstrap del `AgentRegistry` (es liviano, lo necesita el banner y `/agents`).
- Pasar `client`, `agent_id`, `registry` al nuevo `run_cli` sync.
- Eliminar `asyncio.run(...)` del call path chat.
- `TODO(feature/cli-chat-via-rest)` se borra.

Firma nueva (orientativa):

```
def run(global_config, registry, client, agent_id): ...    # sync, no async
```

### B5. Config reading

Ya resuelto en `_build_daemon_client`:
- `global_config.admin.host` + `.port` → `admin_base_url`
- `global_config.admin.auth_key` → `X-Admin-Key`

Se añade (opcional) `global_config.daemon.client.chat_timeout` para el timeout largo de `/turn`. Si no existe, default 300s en `DaemonClient`. Esto requiere un mini-add a `infrastructure/config.py` (dataclass `DaemonClientConfig` o similar) — decisión pequeña que se formaliza en tasks.

**NO** nuevas capas de config. **NO** variables de entorno. YAML only, regla del proyecto.

---

## D. RunAgent — API pública para acceso a historial

### D1. Nuevos métodos en `core/use_cases/run_agent.py`

Añadir dos métodos async públicos al use case `RunAgent`. Son thin delegations al `HistoryRepo` inyectado; el atributo `_history` permanece privado.

```
async def get_history(self) -> list[Message]:
    """Devuelve el historial activo del agente (sin archivados ni infused)."""
    return await self._history.load(self._agent_id)

async def clear_history(self) -> None:
    """Limpia el historial activo del agente."""
    await self._history.clear(self._agent_id)
```

**Rationale**:
- Los adapters (Telegram, admin REST) necesitaban acceder a `run_agent._history` directamente — un acoplamiento a un detalle de implementación privado del use case.
- Exponer `get_history` / `clear_history` como API pública del use case es el patrón correcto en hexagonal: los adapters llaman al caso de uso, nunca a sus colaboradores internos.
- Costo: dos métodos triviales. Beneficio: elimina la deuda en Telegram y previene que se replique en el nuevo router REST.

### D2. Migración del TelegramBot

**Archivo afectado**: `adapters/inbound/telegram/bot.py`

Línea actual (≈80):
```python
await self._container.run_agent._history.clear(self._agent_cfg.id)
```

Línea post-migración:
```python
await self._container.run_agent.clear_history()
```

El método recibe `agent_id` del propio use case (ya lo conoce desde su construcción), por lo que el adapter no necesita pasarlo.

**Alcance**: esta migración ES parte del scope de este change. Dejarla pendiente sólo mueve la deuda de lugar.

---

## C. Cross-cutting

### C1. Error taxonomy

Añadir a `core/domain/errors.py`:

```
UnknownAgentError(DaemonClientError)   # 404 agent_id
DaemonAuthError(DaemonClientError)     # 401/403 X-Admin-Key
```

Las existentes (`DaemonNotRunningError`, `DaemonTimeoutError`, `DaemonClientError`) cubren el resto.

### C1.1 HTTP ↔ domain error ↔ UX mapping

| HTTP status | Cliente levanta           | Mensaje al usuario (CLI)                                                          |
|-------------|---------------------------|-----------------------------------------------------------------------------------|
| ConnectError (no HTTP) | `DaemonNotRunningError` | "El daemon no está corriendo. Iniciá con `inaki daemon` …" (fatal → sale del REPL) |
| TimeoutException       | `DaemonTimeoutError`    | "Timeout esperando respuesta del daemon. Intentá de nuevo." (transient, sigue)    |
| 401 / 403              | `DaemonAuthError`       | "Auth inválida. Verificá `admin.auth_key` en ~/.inaki/config/global.secrets.yaml." (fatal) |
| 404 (agent desconocido)| `UnknownAgentError`     | "Agente '{id}' no existe en el daemon. Disponibles: ..." (fatal para chat; transient si fue un comando puntual) |
| 422 (payload inválido) | `DaemonClientError`     | "Payload rechazado por el servidor: {detail}" (bug del cliente → logs, no debería pasar) |
| 5xx                    | `DaemonClientError`     | "Error interno del daemon (HTTP {status}). Revisá logs con `journalctl -u inaki`." |

**Fatal vs transient en el REPL**: NotRunning y Auth son fatales (el próximo turno también fallará); Timeout y 5xx son transient (puede ser una respuesta larga, el LLM se colgó, etc.).

### C2. Container wiring (`infrastructure/container.py`)

**Sin cambios funcionales** en `AppContainer` / `AgentContainer`. El `DaemonClient` ya se construye en `inaki/cli.py` directamente (no via container) — ese patrón se mantiene porque el CLI es un proceso distinto al daemon. El servidor del daemon no necesita un `DaemonClient` (no se llama a sí mismo).

Si se decide pasar el `chat_timeout` por constructor del DaemonClient, el ajuste es en `cli._build_daemon_client` (leer de config + pasar al constructor). No toca `container.py`.

### C3. Tests scaffolding (layout)

```
tests/unit/core/use_cases/test_run_agent_history_api.py
  - get_history: delega a _history.load(agent_id) y retorna el resultado
  - get_history: lista vacía si no hay mensajes
  - clear_history: delega a _history.clear(agent_id)
  - clear_history: propaga excepciones del repo
  (fixtures existentes: mock_history de tests/conftest.py)

tests/unit/adapters/inbound/telegram/test_bot_clear.py  (o actualizar test existente)
  - _handle_clear llama a run_agent.clear_history() (no a run_agent._history.clear)
  - verificar que el mock es sobre el método público, no el atributo privado

tests/unit/adapters/rest_admin/test_chat_router.py
  - POST /turn happy path (con agent válido, run_agent mockeado)
  - POST /turn agent_id inválido → 404
  - POST /turn sin X-Admin-Key → 401
  - POST /turn cuerpo inválido (message vacío / session_id faltante) → 422
  - GET /history happy (devuelve messages serializados)
  - GET /history agent inválido → 404
  - DELETE /history happy
  - Channel context: verificar que set/reset fue llamado con cli:session_id

tests/unit/adapters/outbound/test_daemon_client_chat.py
  - chat_turn: serializa body correctamente, usa X-Admin-Key, parsea reply
  - chat_turn: ConnectError → DaemonNotRunningError
  - chat_turn: TimeoutException → DaemonTimeoutError
  - chat_turn: 404 → UnknownAgentError
  - chat_turn: 401 → DaemonAuthError
  - chat_history: parsea list[{role, content}]
  - chat_clear: DELETE con query param, 200 OK
  Usa httpx.MockTransport

tests/unit/adapters/inbound/cli/test_cli_runner_rest.py
  - Genera session_id único por run
  - /exit, /quit terminan el loop
  - /history llama a client.chat_history y renderiza
  - /clear llama a client.chat_clear
  - Input normal → chat_turn y muestra reply
  - DaemonNotRunningError en turno → mensaje fatal + sale
  - DaemonTimeoutError → mensaje y sigue loop
  IDaemonClient mockeado, stdin/stdout capturados

tests/integration/test_cli_chat_via_rest.py
  - Levanta admin FastAPI con AppContainer real (agent_config :memory:)
  - DaemonClient real apuntando al TestClient (o uvicorn test port)
  - Ciclo: chat_turn → respuesta, chat_history → incluye ambos mensajes, chat_clear → history vacío
  - Verifica ChannelContext("cli", session_id) llegó al use case
```

Todos los unit tests son aislados (mocks). El integration test usa `:memory:` SQLite (fixture `agent_config` de `tests/conftest.py`) y un LLM stub.

### C4. Logging

El handler `POST /turn` debe emitir:
- INFO al entrar: `"chat_turn agent=%s session=%s msg_len=%d"` (NO el contenido del mensaje — PII potencial).
- INFO al salir (éxito): `"chat_turn done agent=%s session=%s duration_ms=%d reply_len=%d"`.
- WARNING si 4xx cliente (agent no existe, payload malo).
- ERROR con `exc_info=True` si excepción interna del use case.

`GET /history` y `DELETE /history`: INFO con agent_id, duration, count (para history).

DEBUG (opcional, para troubleshooting): loggear primeros 120 chars del mensaje / reply. **Nunca a nivel INFO.**

---

## Config — quick reference

Qué se lee y de dónde:

| Config                      | Origen YAML                                      | Consumidor                   |
|-----------------------------|--------------------------------------------------|------------------------------|
| `admin.host`, `admin.port`  | `~/.inaki/config/global.yaml`                    | `cli.py::_build_daemon_client` |
| `admin.auth_key`            | `~/.inaki/config/global.secrets.yaml`            | idem                         |
| `daemon.client.chat_timeout` (nuevo, opcional) | `~/.inaki/config/global.yaml`    | `cli.py::_build_daemon_client` → `DaemonClient(chat_timeout=…)` |
| `app.default_agent`         | `~/.inaki/config/global.yaml`                    | `cli.py::chat` / `_invoke_default_chat` |

Sin env vars. Sin nuevas capas.

---

## Testing layout (resumen de paths)

- `tests/unit/core/use_cases/test_run_agent_history_api.py` — `get_history` / `clear_history` sobre mock HistoryRepo.
- `tests/unit/adapters/inbound/telegram/test_bot_clear.py` — verificar migración de `_history` a `clear_history()`.
- `tests/unit/adapters/rest_admin/test_chat_router.py` — endpoints con FastAPI TestClient.
- `tests/unit/adapters/outbound/test_daemon_client_chat.py` — httpx MockTransport.
- `tests/unit/adapters/inbound/cli/test_cli_runner_rest.py` — REPL con mock IDaemonClient.
- `tests/integration/test_cli_chat_via_rest.py` — end-to-end con `:memory:` DB.

---

## Open questions (para tasks/apply)

1. **Extraer `_check_admin_auth`** a `adapters/inbound/rest/admin/deps.py` para evitar import cruzado `chat.py → admin.py`. Recomendado pero mecánico.
2. **Nombre del helper nuevo en `DaemonClient`** (`_get`, `_delete`, `_post_long`) — elección estética.
3. **Clave de config del chat timeout** — `daemon.client.chat_timeout` vs `admin.chat_timeout_s` vs `app.chat_timeout`. Propongo `daemon.client.chat_timeout` para agrupar con el resto de config del cliente futuro.
4. **Validación de `session_id`**: ¿forzar formato UUID en el schema (`Field(pattern=UUID_REGEX)`) o aceptar cualquier string no vacío? Recomendado: validar UUID (defensa en profundidad, atrapa bugs del cliente).
5. **¿Mantener `/inspect` y `/consolidate` locales en el CLI?** Ya pasan por daemon hoy (via `client.inspect` / `client.consolidate`). Confirmar que el nuevo REPL los reenvía igual. No es una pregunta abierta de diseño, es un recordatorio para apply.

---

## Trade-offs aceptados

- **Router nuevo sobre admin.py**: elegimos cohesión (router dedicado) sobre "un archivo menos". Facilita tests aislados y futuras variaciones.
- **`session_id` en body JSON** en vez de header `X-Session-Id`: más explícito, visible en logs de debug, consistente con `agent_id` que también va en body. Costo: body un poco más grande — despreciable.
- **Port devuelve `str` / `list[dict]`** en vez de tipos de dominio: mantiene el port desacoplado del HTTP schema y del value object `Message` del dominio. Costo: el CLI renderiza strings directos, no objetos tipados. Aceptable — el CLI ya era "dumb" respecto al contenido.
- **Timeout client-side 300s default**: elegimos "generoso" sobre "ajustado" porque la tool loop en Pi5 puede ser lenta. Costo: un cuelgue real de red se siente mucho antes de fallar. Mitigación: configurable en YAML.
- **Mutación de channel context en AgentContainer con try/finally**: paridad con el comportamiento actual (Telegram, CLI hoy). Es global y no thread-safe de jure; de facto FastAPI serializa suficientemente. Refactor a context-per-request queda out-of-scope.
- **Hard cutover sin feature flag**: elegimos simplicidad/superficie mínima sobre una red de seguridad. El daemon ya es el deployment objetivo (systemd en Pi5); mantener dos paths contradice la propuesta.
- **Port separado de HTTP schema**: duplicación mínima (DTOs Pydantic + port signatures) a cambio de respeto estricto de hexagonal.
- **API pública en `RunAgent` para historial** (`get_history` / `clear_history`): elegimos pagar la deuda de `_history` ahora en lugar de replicarla en el nuevo router REST. Los dos métodos son thin delegations — costo mínimo, beneficio: elimina el acoplamiento en Telegram y previene que el nuevo adapter REST nazca con la misma deuda. `_history` permanece privado; ningún adapter lo toca directamente.

---

## End state

Tras esta feature:
- `inaki chat` NO instancia `AppContainer`. Solo carga `global_config` + `registry` para metadatos y construye un `DaemonClient`.
- El daemon es un prerequisito duro para el chat interactivo, con mensajes de error accionables.
- Todos los turnos pasan por `run_agent.execute()` intacto (tool loop, RAG, memoria) en el proceso del daemon.
- Paridad UX con el estado actual (sin streaming — nunca lo hubo).
- Tres endpoints nuevos, un router nuevo, tres métodos nuevos en el port, dos errores nuevos en el dominio, dos métodos públicos nuevos en `core/use_cases/run_agent.py` (`get_history` / `clear_history`), migración del `TelegramBot` a los métodos públicos, y cero cambios funcionales en `container.py`.
