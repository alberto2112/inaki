# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"          # install with dev deps
ruff check .                     # lint
ruff format .                    # format (line-length 100)
mypy .                           # type check
pytest                           # all tests
pytest tests/unit/               # unit only
pytest tests/integration/        # integration only
pytest -k test_name              # single test
inaki                            # interactive chat (default agent)
inaki chat --agent dev           # specific agent
inaki daemon                     # systemd service mode
```

No Makefile or CI. All commands are direct calls.

## Architecture

Inaki is a multi-agent AI assistant following **strict hexagonal architecture**:

- **`core/`** — Domain layer. Entities, ports (interfaces), use cases, domain services and errors. **NEVER imports from `adapters/` or `infrastructure/`**. Only stdlib + `core/` imports allowed.
- **`adapters/`** — Concrete implementations of ports. Inbound (CLI, Telegram, REST, daemon) and outbound (LLM providers, tools, memory/history repos, embedding, skills, scheduler).
- **`infrastructure/`** — Wiring and cross-cutting. `container.py` is the **single place** where all adapters are instantiated and injected into use cases.
- **`ext/`** — User extensions auto-discovered via `manifest.py`.

Dependency direction: `adapters → core ←  infrastructure`. Never reversed.

### Key Wiring Rules

- **`infrastructure/container.py`** — `AgentContainer` (per-agent DI) and `AppContainer` (root, all agents). Registering a new tool, provider, or repo happens here and ONLY here.
- **Provider discovery** — LLM and embedding providers are auto-discovered by scanning modules for a `PROVIDER_NAME` module-level constant. No manual registration needed.
- **Two-phase agent init** — `AppContainer` first builds all `AgentContainer` instances, then wires delegation (the `delegate` tool) in a second pass so all containers exist before cross-references.

## Configuration

Config lives in **`~/.inaki/`** (not in the repo). First run bootstraps from `config/global.example.yaml`.

**4-layer YAML merge** (each layer overrides only fields it defines):
1. `~/.inaki/config/global.yaml`
2. `~/.inaki/config/global.secrets.yaml`
3. `~/.inaki/config/agents/{id}.yaml`
4. `~/.inaki/config/agents/{id}.secrets.yaml`

Secrets are YAML-only (no env vars). `*.secrets.yaml` files are gitignored.

## Testing

- `pytest-asyncio` mode is `"auto"` — no `@pytest.mark.asyncio` decorator needed
- Shared fixtures in `tests/conftest.py`: `agent_config` (uses `:memory:` DB), `mock_llm`, `mock_memory`, `mock_embedder`, `mock_skills`, `mock_history`, `mock_tools`
- Unit tests mock all adapters; integration tests use real SQLite

## Key Technical Details

- **Embedding dimension is 384** (e5-small ONNX). Changing models requires dropping and recreating the memory DB — no auto-migration.
- **All use cases** are classes with an async `execute()` method, injected via constructor in `container.py`.
- **Tool results** must be `ToolResult` objects, never raw strings.
- **Message roles** use `Role` enum (`Role.USER`, `Role.ASSISTANT`, etc.), not string literals.
- **Workspace containment** — `read_file`, `write_file` y `patch_file` usan `workspace.containment` (strict/warn/off). `shell_exec` NO tiene contención — opera en cualquier path. Ver `docs/configuracion.md`.
- **Tool loop** — LLM can call tools iteratively up to `tools.tool_call_max_iterations` (default 5) with a circuit breaker for repeated failures.
- **Codebase language** — Variables, docstrings, comments, and error messages are in Spanish.
- **Target platform** — Raspberry Pi 5 (ARM64, 4GB RAM) via systemd. See `systemd/inaki.service`.
- **Photo handling** — `ProcessPhotoUseCase` orquesta reconocimiento facial (InsightFace, lazy-load en primera foto) + descripción de escena (LLM multimodal). `IVisionPort.detect_and_embed` devuelve `list[FaceDetection]` (bbox + embedding 512 floats). Ver `docs/face-recognition.md`.
- **InsightFace lazy-load** — El modelo NO se carga al arrancar el daemon. Se carga la primera vez que `IVisionPort.detect_and_embed` es llamado (singleton perezoso en `_get_app()`). Tests verifican esto mockeando el import path del adaptador.
- **faces.db** — Base de datos separada en `~/.inaki/data/faces.db`. Independiente de `history.db` e `inaki.db`. Usa sqlite-vec para embeddings FLOAT[512]. Se crea automáticamente al primer uso.
- **`schema_meta` dimension validation** — Al arrancar, el adapter de visión compara la dimensión del modelo con `schema_meta.embedding_dim` en faces.db. Si no coinciden, lanza `EmbeddingDimensionMismatchError`. Cambiar `faces.model` invalida faces.db — ver `docs/face-recognition.md`.
- **`categoria VARCHAR` pattern** — Las personas ignoradas (via `skip_face`) se persisten en `persons` con `categoria='ignorada'`. Extensible: `NULL` = persona normal, `'ignorada'` = ignorada permanentemente, futuros valores posibles sin ALTER.
- **`message_face_metadata` side-table** — En `history.db`. Key por `history.id`. `ON DELETE CASCADE` limpia metadata cuando se borra el historial.

## Migration Notes

### `multi-agent-telegram-broadcast`

The `history` table was extended with native `channel` and `chat_id` columns. No
auto-migration exists — the DB must be dropped and rebuilt.

Operator steps: stop daemon → `rm ~/.inaki/data/history.db ~/.inaki/data/inaki.db` → add
`channels.telegram.broadcast` config (optional) → restart. See `docs/broadcast-smoke.md`
for the full bootstrap walkthrough.

### `telegram-photo-recognition`

La tabla `message_face_metadata` se agrega como side-table en `history.db`. No hay
auto-migración — la DB debe borrarse y reconstruirse.

Pasos del operador: detener daemon → `rm ~/.inaki/data/history.db ~/.inaki/data/inaki.db` →
agregar bloque `photos:` en `~/.inaki/config/global.yaml` (o dejar `photos: null` para
desactivar) → reiniciar. La DB `faces.db` se crea automáticamente al primer uso.

**Cambio de modelo facial** (`faces.model`): invalida `faces.db` → borrar
`~/.inaki/data/faces.db` y re-enrolar todas las personas. Ver `docs/face-recognition.md`.

### `broadcast-cross-agent-events`

El wire format del broadcast TCP cambió: el `BroadcastMessage` ahora carga `event_type`
(Literal de 3 valores), `sender` y `content` (renombre desde `message`). El HMAC canonical
incluye los nuevos campos, por lo que **versiones viejas y nuevas no son compatibles** —
mensajes con formato distinto se descartan por mismatch silenciosamente.

**Pasos del operador**: detener el daemon en TODOS los Pis del LAN broadcast
simultáneamente → actualizar código → reiniciar. No hay migración de DB. Si un solo Pi
queda atrás, los broadcasts entre él y los actualizados se pierden silenciosamente
(visible en logs como `broadcast.message.dropped.hmac_mismatch`).

**Nuevos flags `broadcast.emit.*`**: defaults backward-compat (`assistant_response=true`,
otros `false`) — sin cambios en config existente, comportamiento idéntico al previo. Para
broadcastear transcripciones de voice o descripciones de fotos, activar `user_input_voice`
y/o `user_input_photo` en UN bot del grupo (ver `docs/configuracion.md`).

### `agent-state-scoped-by-channel-chat`

La tabla `agent_state` en `history.db` pasa de PK `agent_id` a PK compuesta
`(agent_id, channel, chat_id)` y agrega columna `updated_at` para purga futura.
Esto elimina el bleed de sticky skills/tools entre conversaciones distintas del
mismo agente (ej: un grupo de Telegram vs un chat privado ya no comparten estado).

La migración es **automática en caliente** — `_ensure_agent_state_schema()` detecta
el schema legacy en el primer arranque post-deploy y migra los registros existentes
al scope `(agent_id, '', '')` sin pérdida de datos. No se requiere intervención manual.

`save_state` y `load_state` ahora aceptan `channel` y `chat_id` (default `""`).
`clear(channel, chat_id)` borra también el `agent_state` del scope limpiado
(antes solo borraba el historial scoped y dejaba el state intacto).

### `memory-management-tools`

Se exponen al LLM tres tools nuevas (`search_memory`, `delete_memory`,
`update_memory`) y se añaden los métodos `IMemoryRepository.delete()` y
`update()` con soft-delete reversible. Resuelve el caso "borrá esa memoria
errónea" que antes el agente no podía cumplir.

`memories` recibe una columna `deleted INTEGER NOT NULL DEFAULT 0` y el índice
de scope se reescribe como **partial index** sobre `deleted = 0` (más compacto:
solo indexa entries activas). `search`, `search_with_scores` y `get_recent`
filtran `deleted = 0` automáticamente; el `update` y el `delete` operan solo
sobre entries activas (no se permite editar o re-borrar una soft-deleted).

Migración en caliente:

```bash
sqlite3 ~/.inaki/data/inaki.db <<'SQL'
ALTER TABLE memories ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0;
DROP INDEX IF EXISTS idx_memories_scope;
CREATE INDEX idx_memories_scope
  ON memories(agent_id, channel, chat_id, created_at DESC)
  WHERE deleted = 0;
SQL
```

**Bug fix en `store`/`update`**: la tabla virtual `vec0` (`memory_embeddings`)
NO soporta `INSERT OR REPLACE` — el path REPLACE rompe con UNIQUE
constraint. Se reemplaza por `DELETE` + `INSERT`. Esto siempre fue un latent
bug en `store` cuando el mismo id se reescribía.

### `memory-scoped-by-channel-chat`

La tabla `memories` se extiende con columnas `channel TEXT` y `chat_id TEXT` (ambas
nullable) más un índice `(agent_id, channel, chat_id, created_at DESC)`. Cada
`MemoryEntry` extraído ahora se persiste con el scope de la conversación de origen y
el digest markdown se aísla por scope (`mem/digest_{channel}_{chat_id}.md`). Esto evita
que recuerdos de un grupo de Telegram se filtren a un chat privado del mismo agente.

A diferencia de las migraciones previas, **no hace falta borrar `inaki.db`** —
las filas existentes quedan con `channel = NULL` y `chat_id = NULL` (recuerdos
"globales" pre-migración) y siguen siendo recuperables por `search`. Se migra en
caliente con `ALTER TABLE`:

```bash
sqlite3 ~/.inaki/data/inaki.db <<'SQL'
ALTER TABLE memories ADD COLUMN channel TEXT;
ALTER TABLE memories ADD COLUMN chat_id TEXT;
CREATE INDEX IF NOT EXISTS idx_memories_scope
  ON memories(agent_id, channel, chat_id, created_at DESC);
SQL
```

**Cambios de config**: el default de `memory.digest_filename` pasa de
`mem/last_memories.md` a `mem/digest_{channel}_{chat_id}.md`. Si tenés un override
explícito en tu YAML, actualizalo al template — sin placeholders el sistema vuelve a
escribir un único archivo (comportamiento legacy, recuerdos cruzados).

**Cambio semántico de `memory.delay_seconds`**: ahora también se respeta entre
scopes `(channel, chat_id)` dentro del mismo agente, no solo entre agentes en la
consolidación global.

### `background-delegation`

La tool `delegate` ahora es **async por defecto**. El parámetro `wait` controla
el modo: `wait=true` preserva el comportamiento sincrónico legacy
(bloquea hasta que el hijo responde con DelegationResult parseado); `wait=false`
(default) encola la delegación en una cola in-memory bajo un semáforo de 3 y
devuelve `bg-N` al instante. Cuando la delegación termina, el resultado se
inyecta en el `(channel, chat_id)` original via `LLMDispatcherAdapter.dispatch`
como un mensaje `Role.USER` con prefijo `[bg-N] ...`. El agente padre tiene una
sección del system prompt (en inglés) que le explica cómo procesar esos
mensajes — sin saludo, sin preámbulo.

**Sin migración de DB ni cambios de config**. El feature es 100% in-memory: si
el daemon reinicia con tasks in-flight, se pierden silenciosamente (decisión
explícita para uso doméstico Pi 5 — sin retries ni persistencia).

**Lock-per-scope en `LLMDispatcherAdapter`**: la misma instancia se comparte
entre `BackgroundDelegationQueueAdapter` y `SchedulerService` para que ambos
serialicen turnos sobre el mismo `(agent_id, channel, chat_id)`. Resuelve un
race pre-existente latente entre user message + scheduled trigger que el
proyecto reconocía con `extra_sections_snapshot` pero no mitigaba a nivel del
historial.

**IMPORTANTE para mantenedores del scheduler**: el adapter
`LLMDispatcherAdapter` se construye **una sola vez** en `AppContainer.__init__`
y se almacena en `self._llm_dispatcher`. El `SchedulerService` (vía
`SchedulerDispatchPorts.llm_dispatcher`) y el `BackgroundDelegationQueueAdapter`
(vía su param `dispatcher`) reciben **la misma instancia** — por eso comparten
el dict interno de locks-por-scope. Si en el futuro alguien refactoriza esto
construyendo instancias separadas, el lock-per-scope deja de serializar entre
los dos paths y vuelve a aparecer el race condition que mitigamos.

**Breaking para callers que asumían sync**: tests que construyen tool_calls
de `delegate` deben pasar `wait=true` explícitamente para preservar el path
legacy. Tests existentes ya actualizados en `tests/unit/use_cases/test_delegation_integration.py`.

### `in-flight-message-injection`

Mensajes nuevos del usuario sobre un scope `(agent_id, channel, chat_id)` que ya
tiene un `execute()` corriendo ahora se persisten en `history.db` vía
`record_user_message` y el tool loop del turno en curso los drena entre
iteraciones (checkpoints A: antes de `llm.complete`; B: después del batch
completo de `tool_calls`). El LLM ve los mensajes drenados como `role=user`
en `working_messages` en la siguiente llamada y decide la semántica — enriquecer,
corregir, o abortar la tarea. No hay señales especiales: una sección del system
prompt (`_INFLIGHT_CLARIFICATIONS_SECTION` en `run_agent.py`, en inglés) le
explica al LLM cómo interpretarlos.

Cuando el drain devuelve mensajes no-vacíos, el contador `tool_call_max_iterations`
resetea a 0 — sin esto, un enriquecimiento en iter 4/5 dejaría solo 1 iteración
para incorporar el cambio. El `circuit_breaker` NO se resetea (fallos reales de
tools siguen acumulando).

**Componentes nuevos**:
- `core/ports/outbound/scope_registry_port.py` — `IScopeRegistry` con
  `try_mark_busy(scope) -> bool` y `mark_idle(scope) -> None`. Type alias
  `Scope = tuple[str, str, str]`.
- `adapters/outbound/scope_registry_adapter.py` — `InMemoryScopeRegistryAdapter`
  con `set` protegido por un `asyncio.Lock` global. Una sola instancia compartida
  entre todos los agentes (los scopes ya están aislados por `agent_id`).
- `_tool_loop.py` recibe params opcionales `history_store` y `scope`; con
  `None` el comportamiento es legacy (backward-compat 100%).

**Routing en inbound adapters** (`bot.py:_run_pipeline`, `chat.py:chat_turn`,
`agents.py:chat`):
```
if try_mark_busy(scope):
    try: execute() finally: mark_idle(scope)
else:
    record_user_message(message, channel, chat_id)
    return ACK "📝 incorporando a la tarea en curso..."
```

**Sin migración de DB ni cambios de config**. El feature es 100% in-memory: si
el daemon reinicia con scopes marcados busy, todos vuelven a estar libres
(mismo trade-off que `background-delegation` — uso doméstico Pi 5).

**Behavior shift observable**: dos mensajes seguidos del usuario sobre el mismo
scope ahora producen **UNA respuesta combinada** en vez de dos turnos secuenciales.
Antes M2 esperaba a que M1 terminara y disparaba un turno nuevo desde cero
(perdiendo el trabajo previo). Ahora M2 se incorpora al loop en curso.

**Grupos de Telegram EXCLUIDOS**. `_run_group_pipeline` mantiene el flow legacy
con `_schedule_group_flush` + buffer-delay-coalesce + `_extract_trailing_user_batch`.
Razón: durante el delay random NO hay `execute()` corriendo, así que la
"injection in-flight" no aplica. El delay ES su ventana de coalescencia natural.
En `_run_pipeline` el branch in-flight se activa solo cuando `not es_grupo and
user_input is not None` (también skip cuando `user_input=None` para no romper
el path history-derived de fotos enriquecidas).

**Bug aceptado para V1 — race window narrow**: si `execute()` termina exactamente
cuando llega un mensaje nuevo (microsegundos entre `mark_idle` y `try_mark_busy`),
el mensaje puede quedar persistido en history sin que nadie lo procese hasta el
siguiente turno del usuario. Aceptable para uso doméstico (el usuario re-envía
o el próximo mensaje lo trae al loop). Si se vuelve problemático, mitigación
sería re-chequear `try_mark_busy` después del persist y disparar un turno
history-derived si el scope se liberó en el ínterin.

**Costo I/O**: cada iteración del tool loop hace 2 queries adicionales a SQLite
(checkpoints A y B). Para Pi 5 con SQLite local, overhead despreciable (~10-20ms
por turno vs varios segundos del turno completo). Si en el futuro la perf
importara, agregar `IHistoryStore.load_since(after_id)` para leer solo el delta
en vez de toda la historia del scope.

## Git workflow

- Never create a branch without asking me for the name first.
- Never commit without showing me the commit message for approval.
- Always ask before running `git merge` or `git push`.
- Preferred branch naming: `feature/`, `fix/`, `refactor/`, `experiment/`

## References

- **Tech Spec**: `docs/inaki_spec.md`
- **Execution Flow**: `docs/flujo_ejecucion.md`
- **Config Reference**: `docs/configuracion.md`
- **Scheduler Spec**: `docs/scheduler-spec.md`
- **Broadcast Smoke Test**: `docs/broadcast-smoke.md`
- **GitHub**: https://github.com/alberto2112/inaki
