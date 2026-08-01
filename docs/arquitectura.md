# Arquitectura — reglas estructurales

Detalle completo de las reglas de arquitectura de Inaki. `CLAUDE.md` contiene el
resumen operativo; este documento es la fuente de verdad cuando hay que decidir
**dónde va** un componente nuevo.

Índice:

- [Capas y dirección de dependencias](#capas-y-direccion-de-dependencias)
- [Capacidades vs canales — la regla del canal THIN](#capacidades-vs-canales--la-regla-del-canal-thin)
- [Tiers de recursos — harness-global vs per-agente](#tiers-de-recursos--harness-global-vs-per-agente)
- [Reglas de wiring (DI)](#reglas-de-wiring-di)

## Capas y dirección de dependencias

Inaki is a multi-agent AI assistant following **strict hexagonal architecture**:

- **`core/`** — Domain layer. Entities, ports (interfaces), use cases, domain services and errors. **NEVER imports from `adapters/` or `infrastructure/`**. Allowed imports: stdlib, `core/`, and the third-party allowlist `pydantic` + `croniter` + `numpy` (numpy: 512-float face embeddings on Pi 5 — pure Python would be unviable).
- **`adapters/`** — Concrete implementations of ports. Inbound (Telegram, REST admin, interactive CLI chat) and outbound (LLM providers, tools, memory/history repos, embedding, skills, scheduler). **NUNCA importa `infrastructure/`** — si un adapter "necesita" el container o el schema, declara un Protocol/Settings VO de lo que usa y el composition root se lo inyecta.
- **`infrastructure/`** — Wiring and cross-cutting. `container.py` is the **single place** where all adapters are instantiated and injected into use cases.
- **`inaki/`** — **Composition root** (entry points). Acá viven `cli.py`, `daemon_runner.py` y los sub-CLIs (`scheduler_cli`, `knowledge_cli`, `setup_cli`). Está FUERA de la regla hexagonal: un composition root importando `infrastructure/` es legítimo — es su trabajo ensamblar. Los entry points NUEVOS van acá, NO bajo `adapters/inbound/`.
- **`ext/`** — User extensions auto-discovered via `manifest.py`.

Dependency direction: `adapters → core ←  infrastructure`, con `inaki/` (composition root) por encima de todo. Never reversed.
Enforced by `tests/unit/test_architecture.py` (3 reglas, incluyen TYPE_CHECKING e imports locales): (1) `core/` no importa `adapters/` ni `infrastructure/`; (2) terceros en `core/` limitados al allowlist; (3) `adapters/` no importa `infrastructure/`. Las reglas 2 y 3 son **ratchet**: `DEUDA_*` quedó **vacía** el 2026-06-13 (toda la deuda de la auditoría saldada). NUNCA agregar entradas a `DEUDA_*`: resolver el acoplamiento (Settings VOs, Protocols estructurales, o reubicar composition-roots a `inaki/`).

## Capacidades vs canales — la regla del canal THIN

**LEER antes de agregar un canal.**

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
LLM (`media.py` inyecta el bloque `@file <name> ... at <path>`) y el LLM llama la tool
`knowledge_admin`. Un canal nuevo hereda la capacidad GRATIS con solo entregar el
input al pipeline.

**ANTIPATRÓN explícito**: que cada canal nuevo "implemente las pasarelas de los CLI
disponibles". Eso es una explosión N×M (N canales × M capacidades) y multiplica los
composition-roots paralelos. Si te encontrás replicando un comando de CLI dentro de
un canal, parás: la capacidad va a un use case + tool, y el canal solo dispara turnos.
El CLI offline (`inaki/`) puede construir el use case directo para bootstrap sin daemon
— eso es legítimo (es un composition root), no una pasarela en un canal.

**Excepción CERRADA — los slash commands de Telegram** (`commands.py`: `/stop`, `/clear`,
`/consolidate`, `/reconcile`, `/scheduler`, `/ratelimit`, `/reload`, `/chatid`). NO son
la vía de acceso a capacidades: son el **panel de control del OPERADOR** — admin-only por
`allowed_user_ids`, deterministas, sin pasar por el LLM. Existen para cuando el LLM está
ocupado, o cuando querés CERTEZA de que la acción se hizo. Toda capacidad que exponen
está también (y primero) como tool. Reglas: extender un slash **YA existente** con un
sub-comando cuya capacidad ya vive en una tool es aceptable (costo: un port más en
`TelegramBotPorts`) — es lo que se hizo con `/scheduler run <id>` el 2026-07-26, decisión
explícita del operador, con la capacidad ya disponible vía la tool `scheduler`
(`operation: "run"`), `inaki scheduler run` y el REST admin. Crear un slash **NUEVO** para
una capacidad nueva NO: eso es la explosión N×M. Y NUNCA replicar estos slash en un canal
nuevo — un Slack que nazca mañana hereda las TOOLS, no `/scheduler`.

## Tiers de recursos — harness-global vs per-agente

**LEER antes de agregar un recurso con estado.**

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
  se derivan del home**: una 2ª instancia declara `admin.port`/`broadcast.server.port` en su YAML.

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

## Reglas de wiring (DI)

- **`infrastructure/container.py`** — `AgentContainer` (per-agent DI) and `AppContainer` (root, all agents). Registering a new tool, provider, or repo happens here and ONLY here.
- **Settings VOs** — Los use cases NO reciben `AgentConfig`: cada uno declara sus parámetros en un VO de `core/domain/value_objects/agent_settings.py` (`RunAgentSettings`, `OneShotSettings`, `MemorySettings`, `PhotosSettings`). El mapeo config→VO vive en los builders públicos de `container.py` (`build_run_agent_settings`, etc.) — único punto donde ambos mundos se tocan. Para exponer un campo nuevo de config a un use case: agregarlo al VO + al builder.
- **DTOs de adapters outbound** — Mismo patrón hacia el otro lado: los `Resolved*Config` (`ResolvedLLMConfig`, `ResolvedEmbeddingConfig`, `ResolvedTranscriptionConfig`) viven en el `base.py` de su familia de adapters, y los Settings VOs `HistoryStoreSettings` / `ChannelFallbackSettings` junto a su adapter. Las factories/container de infrastructure los componen desde el schema YAML (`LLMProviderFactory.resolve`, mapeos en `container.py`). NUNCA moverlos de vuelta a `infrastructure/config.py` — `adapters/` no importa `infrastructure/`.
- **Provider discovery** — LLM, embedding and transcription providers are auto-discovered by scanning modules for a `PROVIDER_NAME` module-level constant. No manual registration needed. Los tres registries son **independientes** (escanean carpetas distintas: `adapters/outbound/providers/`, `.../embedding/`, `.../transcription/`): que un vendor exista como LLM NO lo hace disponible para transcripción. Transcripción hoy: `groq` y `openai`, ambos OpenAI-compatible (`/audio/transcriptions`), comparten `BaseTranscriptionProvider` — cada concreto solo declara `_DEFAULT_BASE_URL` + `_PROVIDER_LABEL`.
- **Two-phase agent init** — `AppContainer` first builds all `AgentContainer` instances, then wires delegation (the `delegate` tool) in a second pass so all containers exist before cross-references.
- **Delegación — subagente efímero con herencia contra el caller** — El pool de DEFINICIONES de sub-agentes es compartido, pero cada delegación NO usa el `run_agent_one_shot` pre-built del sub: construye una **instancia efímera one-shot resuelta contra el CALLER** vía `AgentContainer.build_ephemeral_child(definition_raw)` (`container.py`). Resolución: `resolve_inherit(_deep_merge(SUBAGENT_DEFAULTS, definition_raw), parent_raw)` con `parent_raw` = config EFECTIVA del caller. El primitivo `inherit` (directiva de merge por bloque, resuelta en dicts crudos ANTES de pydantic y strippeada — NUNCA un campo de modelo) hace que el hijo herede del padre: `llm` por default (vía `SUBAGENT_DEFAULTS`), el resto opt-in. **Tools/recursos = SIEMPRE del caller** (`caller._tools`: workspace/memory/knowledge del padre); el sub recorta el subset visible con `tools.allowed` (filtro REQ-OS-5 en `RunAgentOneShotUseCase`, junto a la exclusión de `delegate` REQ-DG-9). El LLM se REUSA (misma instancia del caller) si la config llm efectiva coincide; si el sub la overridea → `LLMProviderFactory` con los `providers` heredados del caller. SIN embedder (el one-shot expone el toolkit completo sin RAG, REQ-OS-4). Misma def + caller P/Q distintos → instancias independientes heredando cada una de su padre. Ambos paths resuelven el efímero contra el caller: sync (`wire_delegation` arma el closure `build_child` con `get_sub_agent_raw` + `build_ephemeral_child`) y async (`BackgroundDelegationQueueAdapter`, `one_shot_resolver(caller_id, target_id)`). Scope: SOLO `delegate` — el carril de memoria (extractor/reconciliador) hereda por su cuenta vía `merged_llm_config`.
