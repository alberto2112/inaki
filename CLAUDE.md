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

- **`core/`** — Domain layer. Entities, ports (interfaces), use cases, domain services and errors. **NEVER imports from `adapters/` or `infrastructure/`**. Allowed imports: stdlib, `core/`, and the third-party allowlist `pydantic` + `croniter` + `numpy` (numpy: 512-float face embeddings on Pi 5 — pure Python would be unviable).
- **`adapters/`** — Concrete implementations of ports. Inbound (Telegram, REST admin, interactive CLI chat) and outbound (LLM providers, tools, memory/history repos, embedding, skills, scheduler). **NUNCA importa `infrastructure/`** — si un adapter "necesita" el container o el schema, declara un Protocol/Settings VO de lo que usa y el composition root se lo inyecta.
- **`infrastructure/`** — Wiring and cross-cutting. `container.py` is the **single place** where all adapters are instantiated and injected into use cases.
- **`inaki/`** — **Composition root** (entry points). Acá viven `cli.py`, `daemon_runner.py` y los sub-CLIs (`scheduler_cli`, `knowledge_cli`, `setup_cli`). Está FUERA de la regla hexagonal: un composition root importando `infrastructure/` es legítimo — es su trabajo ensamblar. Los entry points NUEVOS van acá, NO bajo `adapters/inbound/`.
- **`ext/`** — User extensions auto-discovered via `manifest.py`.

Dependency direction: `adapters → core ←  infrastructure`, con `inaki/` (composition root) por encima de todo. Never reversed.
Enforced by `tests/unit/test_architecture.py` (3 reglas, incluyen TYPE_CHECKING e imports locales): (1) `core/` no importa `adapters/` ni `infrastructure/`; (2) terceros en `core/` limitados al allowlist; (3) `adapters/` no importa `infrastructure/`. Las reglas 2 y 3 son **ratchet**: `DEUDA_*` quedó **vacía** el 2026-06-13 (toda la deuda de la auditoría saldada). NUNCA agregar entradas a `DEUDA_*`: resolver el acoplamiento (Settings VOs, Protocols estructurales, o reubicar composition-roots a `inaki/`).

### Capacidades vs canales — la regla del canal THIN (LEER antes de agregar un canal)

Una **capacidad** (gestionar knowledge, agendar tareas, gestionar memoria, etc.)
se implementa UNA vez y se expone por TRES superficies que comparten la misma
lógica — NUNCA se re-implementa por canal:

1. **Use case en `core/`** — la lógica vive acá (ej. `core/use_cases/manage_knowledge.py`).
2. **Tool del LLM** (`adapters/outbound/tools/`) — envuelve el use case; le da `routing_keywords` si los humanos la invocan en lenguaje natural. Así el LLM (y por ende CUALQUIER canal) llega a la capacidad.
3. **Gateway admin único** — `POST /admin/tool/invoke` ya invoca cualquier tool; `inaki tool <name>` es su cliente. NO crear endpoints REST por capacidad (sería deuda redundante).

Un **canal** (Telegram, y mañana Slack, etc.) es un **inbound adapter THIN**: solo
traduce su I/O nativo a un turno. **NO implementa pasarelas de CLIs ni lógica de
capacidades.** Ejemplo concreto: "mandar un documento y que entre al RAG" NO tiene
una sola línea de código en Telegram — el canal ya entrega el path del archivo al
LLM (`media.py` inyecta `__FILE__ <name> at <path>`) y el LLM llama la tool
`knowledge_admin`. Un canal nuevo hereda la capacidad GRATIS con solo entregar el
input al pipeline.

**ANTIPATRÓN explícito**: que cada canal nuevo "implemente las pasarelas de los CLI
disponibles". Eso es una explosión N×M (N canales × M capacidades) y multiplica los
composition-roots paralelos. Si te encontrás replicando un comando de CLI dentro de
un canal, parás: la capacidad va a un use case + tool, y el canal solo dispara turnos.
El CLI offline (`inaki/`) puede construir el use case directo para bootstrap sin daemon
— eso es legítimo (es un composition root), no una pasarela en un canal.

### Tiers de recursos — harness-global vs per-agente (LEER antes de agregar un recurso con estado)

Un arnés = **1 daemon = N agentes** (`AgentContainer`). Los recursos con estado se
parten en DOS tiers — y NUNCA en un tercer patrón ad-hoc. Mezclar tiers fue el origen
del caos histórico (algunos recursos aislables per-agente, otros forzados globales, sin
regla escrita).

- **Harness-global (singleton, compartido por TODOS los agentes del proceso):**
  `knowledge`, `scheduler`, `faces`/`photos`. Config SOLO en `GlobalConfig` (NUNCA en
  `AgentConfig`); se construyen UNA vez en `AppContainer`, no por agente. Son los
  singletons pesados (modelo InsightFace en RAM, índice RAG, loop de cron): duplicarlos
  in-process reventaría recursos en la Pi. **No hay aislamiento per-agente para estos —
  es por diseño, no una limitación a resolver.** ¿El usuario final necesita aislar uno?
  → corre **otra instancia del arnés como proceso aparte**, con su propio home de datos.
  El proceso es la frontera de aislamiento shared-nothing. El knob único **`--home` /
  `INAKI_HOME`** re-ancla config+data+`secret.key`+`tool_config`+`users`+knowledge en un
  solo root: `infrastructure/home.py::get_inaki_home()` lo resuelve (override de
  `set_inaki_home` ← flag `--home` → env `INAKI_HOME` → default `~/.inaki`); el validador
  `RuntimePath` y el composition root anclan contra él. **Core/adapters NO importan
  `infrastructure/home`** (ratchet): core recibe `users_dir` por `RunAgentSettings`, los
  adapters reciben paths resueltos (campos `RuntimePath`) o leen `INAKI_HOME` env directo
  (setup TUI, `config_repository`) — el callback de `cli.py` propaga `--home` al env. Los
  configs con `RuntimePath` usados como default de `GlobalConfig` (`scheduler`, `knowledge`)
  usan `Field(default_factory=...)` para resolver en runtime, no al importar. **Puertos NO
  se derivan del home**: una 2ª instancia declara `admin.port`/`broadcast.port` en su YAML.

- **Per-agente (compartir vs aislar es CONFIGURABLE):** `memory`, `history`, `channels`,
  `llm`, `embedding`. Config en `AgentConfig`; se construyen por agente en
  `AgentContainer`. Para memory/history el aislamiento ya está resuelto por dos ejes
  complementarios (granularidades distintas, NO redundantes): **mismo `db_filename` →
  aislados por columna `agent_id`** (toda query filtra por `agent_id`;
  `sqlite_history_store.py` arranca el WHERE con `agent_id = ?`; memoria usa índice de
  scope `(agent_id, channel, chat_id)`) → **cero bleed entre agentes que comparten
  fichero**; **`db_filename` distinto → aislamiento físico de fichero.** NO agregar una
  abstracción formal de "pools" encima: para 2 recursos es over-engineering.

**Regla al agregar un recurso con estado nuevo:** decidí su tier ANTES de escribir
código. Singleton pesado compartido → `GlobalConfig` + `AppContainer`. Per-conversación
o per-agente → `AgentConfig` + `AgentContainer`, aislable por `agent_id`/fichero. NUNCA
un `knowledge` o `scheduler` per-agente: rompe el tier y multiplica recursos.

### Key Wiring Rules

- **`infrastructure/container.py`** — `AgentContainer` (per-agent DI) and `AppContainer` (root, all agents). Registering a new tool, provider, or repo happens here and ONLY here.
- **Settings VOs** — Los use cases NO reciben `AgentConfig`: cada uno declara sus parámetros en un VO de `core/domain/value_objects/agent_settings.py` (`RunAgentSettings`, `OneShotSettings`, `MemorySettings`, `PhotosSettings`). El mapeo config→VO vive en los builders públicos de `container.py` (`build_run_agent_settings`, etc.) — único punto donde ambos mundos se tocan. Para exponer un campo nuevo de config a un use case: agregarlo al VO + al builder.
- **DTOs de adapters outbound** — Mismo patrón hacia el otro lado: los `Resolved*Config` (`ResolvedLLMConfig`, `ResolvedEmbeddingConfig`, `ResolvedTranscriptionConfig`) viven en el `base.py` de su familia de adapters, y los Settings VOs `HistoryStoreSettings` / `ChannelFallbackSettings` junto a su adapter. Las factories/container de infrastructure los componen desde el schema YAML (`LLMProviderFactory.resolve`, mapeos en `container.py`). NUNCA moverlos de vuelta a `infrastructure/config.py` — `adapters/` no importa `infrastructure/`.
- **Provider discovery** — LLM and embedding providers are auto-discovered by scanning modules for a `PROVIDER_NAME` module-level constant. No manual registration needed.
- **Two-phase agent init** — `AppContainer` first builds all `AgentContainer` instances, then wires delegation (the `delegate` tool) in a second pass so all containers exist before cross-references.
- **Delegación — subagente efímero con herencia contra el caller** — El pool de DEFINICIONES de sub-agentes es compartido, pero cada delegación NO usa el `run_agent_one_shot` pre-built del sub: construye una **instancia efímera one-shot resuelta contra el CALLER** vía `AgentContainer.build_ephemeral_child(definition_raw)` (`container.py`). Resolución: `resolve_inherit(_deep_merge(SUBAGENT_DEFAULTS, definition_raw), parent_raw)` con `parent_raw` = config EFECTIVA del caller. El primitivo `inherit` (directiva de merge por bloque, resuelta en dicts crudos ANTES de pydantic y strippeada — NUNCA un campo de modelo) hace que el hijo herede del padre: `llm` por default (vía `SUBAGENT_DEFAULTS`), el resto opt-in. **Tools/recursos = SIEMPRE del caller** (`caller._tools`: workspace/memory/knowledge del padre); el sub recorta el subset visible con `tools.allowed` (filtro REQ-OS-5 en `RunAgentOneShotUseCase`, junto a la exclusión de `delegate` REQ-DG-9). El LLM se REUSA (misma instancia del caller) si la config llm efectiva coincide; si el sub la overridea → `LLMProviderFactory` con los `providers` heredados del caller. SIN embedder (el one-shot expone el toolkit completo sin RAG, REQ-OS-4). Misma def + caller P/Q distintos → instancias independientes heredando cada una de su padre. Ambos paths resuelven el efímero contra el caller: sync (`wire_delegation` arma el closure `build_child` con `get_sub_agent_raw` + `build_ephemeral_child`) y async (`BackgroundDelegationQueueAdapter`, `one_shot_resolver(caller_id, target_id)`). Scope: SOLO `delegate` — el carril de memoria (extractor/reconciliador) hereda por su cuenta vía `merged_llm_config`.

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
- **RunAgent — fases del turno** — `RunAgentUseCase._execute_turn` es un orquestador delgado: las fases (semantic routing + sticky, knowledge pre-fetch, presupuesto de tokens, ensamblado de mensajes, secciones in-flight, debug de foto) viven como funciones libres en `core/use_cases/_turn_pipeline.py` — mismo contrato que `_tool_loop.py`: dependencias explícitas (ports, settings VO, VOs), sin `self`, testeables aisladas. `run_semantic_routing` devuelve un `RoutingOutcome` (incluye `query_vec` para reusar en `prefetch_knowledge`, que también comparte `inspect()`). Para tocar una fase: editar la función en `_turn_pipeline.py`, NO re-inline en el use case.
- **Tool results** must be `ToolResult` objects, never raw strings.
- **Tool Config Protocol** — Tools que necesitan credenciales configurables por chat declaran `config_namespace` en la clase y reciben `config_store: IToolConfigStore` en el constructor (inyectado por `container.py`, también para tools de `ext/`). Persistencia en `tool_config.{namespace}` de **`config/tool_config.yaml`** — archivo PROPIO del store (dueño: el daemon), NO `global.secrets.yaml` (ese es del operador y el daemon no lo pisa). El store **lee su propio archivo al construirse** (la config sobrevive al reinicio) y `tool_config` NO participa del merge de 4 capas. Sensibles cifrados `enc:` con `~/.inaki/secret.key`. NUNCA crear un YAML de config propio por tool — eso era el patrón legacy (4 islas eliminadas); el archivo único compartido por namespace NO es una isla.
- **Message roles** use `Role` enum (`Role.USER`, `Role.ASSISTANT`, etc.), not string literals.
- **TelegramBot — estructura** — `bot.py` conserva wiring + auth + turno privado (`_run_pipeline`); los handlers viven en mixins por responsabilidad (`commands.py`, `media.py`, `group_flow.py`, `broadcast.py`), cada uno declarando el slice de estado que consume como anotaciones de clase (contrato mypy). El bot NO recibe `AgentContainer`/`AgentConfig`: recibe `TelegramBotPorts` + `TelegramBotSettings` (`ports.py`, tipados contra core), construidos por `build_telegram_bot_settings/ports` en `container.py`. Todo el estado se inicializa en `TelegramBot.__init__`.
- **Workspace containment** — `read_file`, `write_file` y `patch_file` usan `workspace.containment` (strict/warn/off). `shell_exec` NO tiene contención — opera en cualquier path. Ver `docs/configuracion.md`.
- **Tool loop** — LLM can call tools iteratively up to `tools.tool_call_max_iterations` (default 5) with a circuit breaker for repeated failures.
- **Scheduler cron evaluation** — TODA computación de "próxima ocurrencia" de un cron pasa por `core/domain/utils/cron.py::next_cron_occurrence()` (evalúa en `user.timezone`, devuelve UTC). NUNCA llamar `croniter` directo para next_run: evaluar cron en dos lugares con tz distintas causó el bug histórico de doble ejecución separada por el offset DST (repo en local, service en UTC).
- **Tool semantic routing** — ALL tools (including builtins) go through RAG selection when `len(all_schemas) > tools.semantic_routing_min_tools` (default 10). There is NO automatic injection of builtins. Only `top_k` (default 5) tools reach the LLM per turn.
- **`ITool.routing_keywords`** — Optional field (default `""`). Content is concatenated with `description` **only for embedding** — never sent to the LLM schema. Pattern: `description` in English (LLM comprehension), `routing_keywords` in multilingual es/en/fr (retrieval). Reason: `multilingual-e5-small` matches query↔text much better within the same language than cross-lingual. Use this for tools that users invoke with natural language (scheduler, web_search, memory). Omit for tools the LLM selects by reasoning (FS tools, delegate, create_tool). Cache hash includes both fields — changing either invalidates the embedding cache.
- **Knowledge — read-only vs indexable** — `IKnowledgeSource` (search) es read-only por Liskov. Las fuentes gestionables (solo `DocumentKnowledgeSource`) implementan `IIndexableKnowledgeSource` (index/ingest_file/list_files/delete_file/get_stats). La gestión (ingest/reindex/list/stats/delete) vive en `ManageKnowledgeUseCase` (recibe la **lista viva** de fuentes del orchestrator → ve las de extensiones), expuesta al LLM por la tool `knowledge_admin` y al operador por `inaki knowledge ...`. Ingest = modelo inbox: copia el archivo a la carpeta de la fuente e indexa **ignorando el glob**. Telegram NO tiene código de knowledge (ver "regla del canal THIN"). NUNCA agregar `index()` a `IKnowledgeSource`: rompería las fuentes read-only (memoria, sqlite).
- **Codebase language** — Variables, docstrings, comments, and error messages are in Spanish.
- **Target platform** — Raspberry Pi 5 (ARM64, 4GB RAM) via systemd. See `systemd/inaki.service`.
- **Photo handling** — `ProcessPhotoUseCase` orquesta reconocimiento facial (InsightFace, lazy-load en primera foto) + descripción de escena (LLM multimodal). `IVisionPort.detect_and_embed` devuelve `list[FaceDetection]` (bbox + embedding 512 floats). Ver `docs/face-recognition.md`.
- **InsightFace lazy-load** — El modelo NO se carga al arrancar el daemon. Se carga la primera vez que `IVisionPort.detect_and_embed` es llamado (singleton perezoso en `_get_app()`). Tests verifican esto mockeando el import path del adaptador.
- **faces.db** — Base de datos separada en `~/.inaki/data/faces.db`. Independiente de `history.db` e `inaki.db`. Usa sqlite-vec para embeddings FLOAT[512]. Se crea automáticamente al primer uso.
- **`schema_meta` dimension validation** — Al arrancar, el adapter de visión compara la dimensión del modelo con `schema_meta.embedding_dim` en faces.db. Si no coinciden, lanza `EmbeddingDimensionMismatchError`. Cambiar `faces.model` invalida faces.db — ver `docs/face-recognition.md`.
- **`categoria VARCHAR` pattern** — Las personas ignoradas (via `skip_face`) se persisten en `persons` con `categoria='ignorada'`. Extensible: `NULL` = persona normal, `'ignorada'` = ignorada permanentemente, futuros valores posibles sin ALTER.
- **`message_face_metadata` side-table** — En `history.db`. Key por `history.id`. `ON DELETE CASCADE` limpia metadata cuando se borra el historial.

## Migration Notes

### `channel-send-history-persist`

El trigger `channel_send` ahora **persiste el texto enviado en el historial**
(`history.db`) del agente dueño, no solo en `task_logs.metadata`. Antes era el
único canal por el que el asistente "hablaba" sin dejar rastro en su propia
conversación — asimetría con `agent_send`, que ya persistía su intercambio vía
`llm_dispatcher`. Si el usuario respondía a un `channel_send`, el agente no
tenía contexto de lo que había mandado.

**Cuándo persiste**: solo si el `resolved_target` (tras la cascada del router)
apunta a un **canal conversacional vivo** — su prefijo está entre los sinks
nativos (`native_sinks`, hoy `{telegram}`) — **y** la tarea tiene `created_by`
no vacío. Se persiste un `Message(role=ASSISTANT)` en el scope
`(created_by, channel, chat_id)` parseado del `resolved_target` (donde el
usuario REALMENTE vio el mensaje, no el target original). **Cuándo NO**: cayó al
fallback de archivo (no es canal real), `created_by` vacío (origen CLI sin
agente dueño), o corrida manual (`run_task_now`/`ephemeral=True`, para no
ensuciar la conversación real al testear).

**Componentes nuevos**:
- `IChannelHistoryRecorder` (port en `scheduler_dispatch_port.py`) — campo nuevo
  en `SchedulerDispatchPorts`. El `SchedulerService` solo delega; el recorder es
  el único que conoce qué canales son conversacionales y cómo resolver el
  historial por `agent_id`.
- `ChannelHistoryRecorderAdapter` (`adapters/outbound/scheduler/dispatch_adapters.py`)
  — sigue el patrón de `LLMDispatcherAdapter`: recibe el dict de agentes
  duck-typed (`adapters` no importa `infrastructure`) y resuelve `agent.history`
  por id. Recibe el set de canales conversacionales (= `set(native_sinks)`).
- `AgentContainer.history` (property pública nueva) para que el recorder acceda
  al `SQLiteHistoryStore` del agente, igual que `run_agent`.

**Sin migración de DB ni cambios de config**. La columna `(channel, chat_id)` de
`history.db` ya existía. El mensaje persistido fluye por memoria como cualquier
otro `assistant` message (se consolidará en su scope). Backward-compat: tareas
sin `created_by` (CLI) y canales no nativos se comportan igual que antes.

### `subagent-inheritance`

El flujo `delegate` dejó de ejecutar el `run_agent_one_shot` pre-built del
sub-agente (que corría con la config resuelta contra `global`). Ahora cada
delegación construye una **instancia efímera resuelta contra el CALLER**
(`AgentContainer.build_ephemeral_child`): el hijo hereda el `llm` del padre por
default (primitivo `inherit` + `SUBAGENT_DEFAULTS`), opera con las tools/recursos
del padre (`caller._tools`), y puede acotar el subset visible con el campo nuevo
`tools.allowed`. La misma definición de sub delegada por P y por Q hereda LLMs
distintos (per-caller, no per-definición).

**Sin migración de DB ni cambios de config obligatorios.** Es 100% in-memory y
backward-compat para configs existentes: un sub sin `tools.allowed` ve todo el
toolkit del caller; sin override de `llm` hereda la instancia del padre. El campo
`tools.allowed` (lista de nombres; `None`/ausente = sin restricción) SOLO tiene
efecto en el flujo `delegate` (one-shot sin RAG) — en el turno normal es inerte.

**Behavior shift observable**: un sub-agente que antes corría con el `llm` /
`workspace` declarados en SU YAML (resueltos contra `global`) ahora hereda los del
caller. Si un sub necesita un `llm` propio, debe declararlo en su delta (override)
→ se construye vía `LLMProviderFactory` con los `providers` (credenciales)
heredados del caller. El `run_agent_one_shot` pre-built de cada container sigue
existiendo pero ya NO se usa en el path `delegate`.

### `tool-config-own-file`

El store del Tool Config Protocol dejó de vivir dentro de `global.secrets.yaml`
y pasó a su **propio archivo daemon-owned**: `config/tool_config.yaml`. Razón:
`global.secrets.yaml` lo escribe el operador a mano (api keys de providers,
tokens) pero el daemon le reescribía el bloque `tool_config:` en runtime — dos
dueños en un archivo, dolor para quien despliega. Ahora el operador recupera
`global.secrets.yaml` como archivo de SOLO credenciales que el daemon no toca.

**Fix de bug incluido**: `load_global_config` construía `GlobalConfig` SIN pasar
`tool_config=`, así que `global_config.tool_config` salía SIEMPRE `{}` y el store
(que se sembraba de ahí vía `initial=`) nunca leía el disco al arrancar → tras
cualquier reinicio del daemon TODA la config de tools (exchange, web_search…) era
invisible en memoria hasta reconfigurar. Se resuelve por diseño: el store ahora
**lee su propio `tool_config.yaml`** en `__init__` (sin `initial`, sin depender
del loader). El campo `GlobalConfig.tool_config` se eliminó (estaba muerto).

**Migración automática en caliente** (`migrate_tool_config_to_own_file` en
`config_loader.py`, llamada desde `ensure_user_config` y desde
`AppContainer._init_shared_state`): al arrancar, si existe el bloque `tool_config:`
en `global.secrets.yaml` y no existe `config/tool_config.yaml`, mueve el bloque al
archivo nuevo y lo limpia del secrets (preserva el resto + comentarios, ruamel).
Orden seguro: escribe el archivo nuevo ANTES de limpiar el viejo — peor caso,
duplicado benigno (el store solo lee `tool_config.yaml`), nunca pérdida. La
`secret.key` NO cambia → los `enc:` siguen descifrándose, sin reconfigurar.

**Sin pasos manuales del operador.** Si se desea, tras verificar que
`config/tool_config.yaml` quedó poblado, el bloque viejo ya no está en
`global.secrets.yaml`. No tocar el `secrets_path` del store de vuelta a
`global.secrets.yaml`: el archivo propio es la decisión.

### `tool-config-protocol`

Se eliminaron las 4 islas de configuración per-tool (`web_search_config.yaml`,
`exchange_config.yaml`, `fal_music_config.yaml`, `replicate_music_config.yaml` —
cada una con su propio YAML + `CryptoService`), `CryptoService` mismo (Fernet +
`~/.inaki/.env`, único habitante de `core/services/`), el wizard
`inaki setup secret-key` (`setup_wizard.py`) y la dependencia `python-dotenv`.
El wizard escribía la clave en `{repo}/.env` mientras `CryptoService` la leía
de `~/.inaki/.env` — nunca fueron el mismo archivo.

Reemplazo: **Tool Config Protocol** (`core/ports/outbound/tool_config_port.py`,
`IToolConfigStore` sync). La función de configurar credenciales conversando con
el agente (`operation=configure` / `show_config`) se PRESERVA — lo que cambia
es el storage: todo va al bloque `tool_config.{namespace}` de
`global.secrets.yaml` (sistema de 4 capas), con campos sensibles cifrados
(Fernet, prefijo `enc:`, clave auto-generada en `~/.inaki/secret.key` 0600).
`YamlToolConfigStore` (adapters — `cryptography` NO vuelve al core) escribe con
ruamel preservando comentarios; los writes son efectivos al instante sin
reiniciar. Una tool adopta el protocolo declarando `config_namespace` (class
attr de `ITool`) — el container la instancia con `config_store=...` (aplica
también a tools de `ext/`, cuyo contrato deja de ser estrictamente zero-arg).
Ver `docs/configuracion.md` → "Tool Config Protocol".

**Pasos del operador**: borrar los archivos huérfanos
(`~/.inaki/config/{web_search,exchange,fal_music,replicate_music}_config.yaml`,
`~/.inaki/.env`). Las credenciales viejas cifradas no son recuperables — basta
decirle la key al agente por chat (ej: "configurá web_search con la key tvly-...")
o escribirla a mano bajo `tool_config:` en `global.secrets.yaml`.
`DEUDA_TERCEROS_CORE` en `test_architecture.py` quedó vacía y debe mantenerse así.

### `drop-per-agent-rest`

La superficie REST per-agente (`channels.rest`: un puerto uvicorn por agente,
auth `X-API-Key`) se eliminó. Toda la superficie HTTP vive en el **admin server**
(un puerto global, ruteo por `agent_id`, auth `X-Admin-Key`). Equivalencias:

| Per-agente (eliminado) | Admin server |
|---|---|
| `POST /chat` | `POST /admin/chat/turn` |
| `GET /info` | `GET /admin/agent/info?agent_id=X` |
| `GET /history` / `DELETE /history` | `GET`/`DELETE /admin/chat/history?agent_id=X` |
| `POST /consolidate` | `POST /consolidate` con body `{"agent_id": "X"}` (sin agent_id consolida todos) |

Bloques `channels.rest` en YAML existentes se ignoran silenciosamente (el dict
de channels admite claves arbitrarias). La validación de colisión de puertos
REST entre agentes se eliminó de `config.py` junto con la superficie.

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

### `per-user-context-files`

El archivo global `~/.inaki/USER.md` se reemplaza por archivos per-user scopeados
por canal. `RunAgentUseCase._read_user_context` ahora resuelve contra el
`ChannelContext` del turno:

```
~/.inaki/users/{channel_type}/{username}.md   ← preferente
~/.inaki/users/{channel_type}/{user_id}.md    ← fallback
(nada)                                        ← si ninguno existe
```

**Razón**: el bot va pisando dirección multiusuario (Telegram con varios
remitentes humanos), y un único `USER.md` global mezclaba contexto. Ahora cada
`(channel, identidad)` carga su propio archivo.

**Sin auto-detección de legacy**. El soporte a `~/.inaki/USER.md` se borra sin
warning ni fallback — coherente con "no sobreingeniar" para uso doméstico.
Migración manual del operador:

```bash
mv ~/.inaki/USER.md ~/.inaki/users/telegram/{tu_username}.md
# Para chat por CLI/REST opcionalmente:
cp ~/.inaki/users/telegram/{tu_username}.md ~/.inaki/users/cli/{tu_user}.md
```

**Auto-creación de subdirs por canal**: el daemon, al arrancar, ejecuta
`ensure_user_channel_dirs(home, registry.list_all())` y crea
`~/.inaki/users/{channel}/` por cada canal configurado en cualquier agente.
Idempotente, errores de OS loguean WARNING sin abortar arranque. Se invoca
también en cada reload (`bootstrap_fn` del daemon) para captar canales nuevos.
Sin sentido detectar "canal con humanos" vs "canal interno" — el costo es
nulo y simplifica.

**Wiring CLI/REST**: el admin chat router (`/admin/chat/turn`) lee
`channels.cli.user` del YAML del agente y lo inyecta como `username` en el
`ChannelContext`. Sin esa entrada, el lookup cae al fallback por `user_id`
(`session_id` del cliente) que normalmente no tiene archivo → sin contexto.

**Telegram ya estaba listo**: el bot pobla `username` y `user_id` en el
`ChannelContext` desde `update.message.from_user` (privados). En grupos
`username=None` y `user_id=agent_id` → no se carga contexto per-user, lo cual es
correcto (la identidad por mensaje va embebida en el contenido vía
`format_group_message`).

**Defensa contra path traversal**: si `username` o `user_id` contienen `/`, `\`
o `..`, ese candidato se descarta. Paranoia barata — los valores vienen del
canal, pero no costaba nada chequear.

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

### `telegram-group-auth`

La matriz de autorización del canal Telegram se separó por contexto. El guardián
único es `TelegramBot._is_authorized(update)`, que compone los dos building blocks
existentes según el tipo de chat:

- **Privado**: filtra por `allowed_user_ids` (lista vacía = todos). Sin cambios.
- **Grupo**: filtra SOLO por `allowed_chat_ids`. `allowed_user_ids` ya **no aplica**
  en grupos — cualquier miembro de un grupo autorizado puede interactuar.
- **`allowed_chat_ids` vacío**: el bot **NO responde en grupos** (solo privados).

**Breaking change de comportamiento** (sin migración de DB ni de config): antes,
`allowed_chat_ids: []` significaba "todos los grupos aceptados" — el código
contradecía su propio docstring. Ahora vacío = ningún grupo. Configs que tenían
`allowed_chat_ids: []` y dependían de responder en grupos **dejan de hacerlo**.

**Paso del operador**: para seguir respondiendo en un grupo, agregar su `chat_id`
a `channels.telegram.allowed_chat_ids` (obtenible con `/chatid` dentro del grupo).

La matriz aplica uniforme a los 4 handlers de mensaje (texto, foto, voz, media
silenciosa). Los **comandos slash** (`/start`, `/clear`, `/scheduler`, etc.) quedan
fuera: siguen siendo admin-only por `allowed_user_ids` vía `_is_allowed`, incluso
en grupos autorizados (`/chatid` mantiene su bypass de `allowed_chat_ids`).

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
