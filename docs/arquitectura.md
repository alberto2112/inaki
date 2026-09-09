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

- **`inaki/kernel/`** — El kernel. El turno (`run_agent`, tool loop), the ports it consumes, entities, value objects and domain services. **NEVER imports a feature module or the composition root**. Allowed imports: stdlib, `core/`, and the third-party allowlist `pydantic` + `croniter` + `numpy` (numpy: 512-float face embeddings on Pi 5 — pure Python would be unviable).
- **módulos de `inaki/`** — Un paquete por feature (`llm`, `embedding`, `tools`, `skills`, `memory`, `knowledge`, `scheduler`, `agents`, `perception`, `extensions`, `config`, `observability`) y un paquete por canal bajo `inaki/channels/`. Implementan los ports que el kernel declara. **NUNCA importan el composition root** ni otro módulo salvo lo que su contrato de `import-linter` permite — si un módulo "necesita" el container o el schema, declara un Protocol/Settings VO de lo que usa y el composition root se lo inyecta.
- **`wiring.py` de cada módulo** — La factory que compone los adapters del módulo desde la config (`inaki/llm/wiring.py`, `inaki/embedding/wiring.py`, `inaki/perception/wiring.py`): el ÚNICO fichero de un módulo con permiso para importar `inaki.config`, declarado como excepción en su contrato.
- **`inaki/app/assembly.py`** — El composition root: `ensamblar()` llama al `wiring.py` de cada módulo en cinco pasadas explícitas y entrega runtimes tipados e inmutables (`inaki/app/runtime.py`: `AgentRuntime`, `HarnessRuntime`). No instancia adapters: eso lo hace cada módulo; este fichero decide cuándo y con qué se inyectan into use cases.
- **`inaki/`** — **Composition root** (entry points). `inaki/cli/` tiene un módulo por comando (`chat`, `daemon`, `admin`, `tool`, `send`, `scheduler`, `knowledge`) con los helpers en `_common`; `inaki/app/` tiene el bootstrap, el runner del daemon y el reloader. Está FUERA de la regla hexagonal: un composition root importando a todos es legítimo — es su trabajo ensamblar. Los entry points NUEVOS van a `inaki/cli/`; los canales (Telegram, REST admin, CLI interactivo) son paquetes bajo `inaki/channels/`.
- **`ext/`** — User extensions auto-discovered via `manifest.py`.

Dependency direction: `composition root (inaki/app, inaki/cli) → módulos → kernel`. Never reversed.
Enforced by two tools. `lint-imports` (`[tool.importlinter]` in `pyproject.toml`) is the law between modules: what each one may import, and which ones are independent of each other. `tests/kernel/test_terceros_del_kernel.py` (incluye TYPE_CHECKING e imports locales) guards the one rule import-linter cannot express: terceros en el kernel limitados al allowlist. Es **ratchet**: `DEUDA_TERCEROS_KERNEL` quedó **vacía** el 2026-06-13. NUNCA agregar entradas a `DEUDA_*`: resolver el acoplamiento (Settings VOs, Protocols estructurales, o reubicar composition-roots a `inaki/`).

## Capacidades vs canales — la regla del canal THIN

**LEER antes de agregar un canal.**

Una **capacidad** (gestionar knowledge, agendar tareas, gestionar memoria, etc.)
se implementa UNA vez y se expone por TRES superficies que comparten la misma
lógica — NUNCA se re-implementa por canal:

1. **Use case en su módulo** — la lógica vive acá (ej. `inaki/knowledge/use_cases/manage_knowledge.py`).
2. **Tool del LLM** (`inaki/tools/builtin/`, o `tools/` del módulo dueño) — envuelve el use case; le da `routing_keywords` si los humanos la invocan en lenguaje natural. Así el LLM (y por ende CUALQUIER canal) llega a la capacidad.
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

Un arnés = **1 daemon = N agentes** (`AgentRuntime`). Los recursos con estado se
parten en DOS tiers — y NUNCA en un tercer patrón ad-hoc. Mezclar tiers fue el origen
del caos histórico (algunos recursos aislables per-agente, otros forzados globales, sin
regla escrita).

- **Harness-global (singleton, compartido por TODOS los agentes del proceso):**
  `knowledge`, `scheduler`, `faces`/`photos`. Config SOLO en `GlobalConfig` (NUNCA en
  `AgentConfig`); se construyen UNA vez en la pasada 2 del ensamblador (viven en el `HarnessRuntime`), no por agente. Son los
  singletons pesados (modelo InsightFace en RAM, índice RAG, loop de cron): duplicarlos
  in-process reventaría recursos en la Pi. **No hay aislamiento per-agente para estos —
  es por diseño, no una limitación a resolver.** ¿El usuario final necesita aislar uno?
  → corre **otra instancia del arnés como proceso aparte**, con su propio home de datos.
  El proceso es la frontera de aislamiento shared-nothing. El knob único **`--home` /
  `INAKI_HOME`** re-ancla config+data+`secret.key`+`tool_config`+`users`+knowledge en un
  solo root: `inaki/config/home.py::get_inaki_home()` lo resuelve (override de
  `set_inaki_home` ← flag `--home` → env `INAKI_HOME` → default `~/.inaki`); el validador
  `RuntimePath` y el composition root anclan contra él. **Core y los módulos NO resuelven
  el home**: core recibe `users_dir` por `RunAgentSettings`, los módulos reciben paths
  resueltos (campos `RuntimePath`) o leen `INAKI_HOME` env directo
  (`config_repository`) — el callback de `cli.py` propaga `--home` al env. Los
  configs con `RuntimePath` usados como default de `GlobalConfig` (`scheduler`, `knowledge`)
  usan `Field(default_factory=...)` para resolver en runtime, no al importar. **Puertos NO
  se derivan del home**: una 2ª instancia declara `admin.port`/`broadcast.server.port` en su YAML.

- **Per-agente (compartir vs aislar es CONFIGURABLE):** `memory`, `history`, `channels`,
  `llm`, `embedding`. Config en `AgentConfig`; se construyen por agente en
  pasada 1 del ensamblador (`AgentRuntime`). Para memory/history el aislamiento ya está resuelto por dos ejes
  complementarios (granularidades distintas, NO redundantes): **mismo `db_filename` →
  aislados por columna `agent_id`** (toda query filtra por `agent_id`;
  `sqlite_history_store.py` arranca el WHERE con `agent_id = ?`; memoria usa índice de
  scope `(agent_id, channel, chat_id)`) → **cero bleed entre agentes que comparten
  fichero**; **`db_filename` distinto → aislamiento físico de fichero.** NO agregar una
  abstracción formal de "pools" encima: para 2 recursos es over-engineering.

**Regla al agregar un recurso con estado nuevo:** decidí su tier ANTES de escribir
código. Singleton pesado compartido → `GlobalConfig` + `HarnessRuntime`. Per-conversación
o per-agente → `AgentConfig` + `AgentRuntime`, aislable por `agent_id`/fichero. NUNCA
un `knowledge` o `scheduler` per-agente: rompe el tier y multiplica recursos.

## Reglas de wiring (DI)

- **`inaki/app/assembly.py` + `inaki/app/runtime.py`** — `ensamblar()` (five passes, see `flujo_ejecucion.md`) produces an `AgentRuntime` per agent and one `HarnessRuntime`. A new tool, provider or repo is built by its module's `wiring.py`; the assembler only decides where in the order it goes.
- **Settings VOs** — Los use cases NO reciben `AgentConfig`: cada uno declara sus parámetros en un VO de `inaki/kernel/domain/value_objects/agent_settings.py` (`RunAgentSettings`, `OneShotSettings`, `MemorySettings`, `PhotosSettings`). El mapeo config→VO vive en los builders públicos de `container.py` (`build_run_agent_settings`, etc.) — único punto donde ambos mundos se tocan. Para exponer un campo nuevo de config a un use case: agregarlo al VO + al builder.
- **DTOs de adapters outbound** — Mismo patrón hacia el otro lado: los `Resolved*Config` (`ResolvedLLMConfig`, `ResolvedEmbeddingConfig`, `ResolvedTranscriptionConfig`) viven en el `base.py` de su módulo (`inaki/llm`, `inaki/embedding`, transcripción en `inaki/perception`), y los Settings VOs `HistoryStoreSettings` / `ChannelFallbackSettings` junto a su adapter. El `wiring.py` de cada módulo y el container los componen desde el schema YAML (`LLMProviderFactory.resolve`, mapeos en `container.py`). NUNCA moverlos de vuelta a `inaki/config/` — `adapters/` no importa `infrastructure/`.
- **Provider discovery** — LLM, embedding and transcription providers are auto-discovered by scanning modules for a `PROVIDER_NAME` module-level constant. No manual registration needed. Los tres registries son **independientes** (escanean paquetes distintos: `inaki/llm/`, `inaki/embedding/`, `inaki/perception/adapters/transcription/`): que un vendor exista como LLM NO lo hace disponible para transcripción. Transcripción hoy: `groq` y `openai`, ambos OpenAI-compatible (`/audio/transcriptions`), comparten `BaseTranscriptionProvider` — cada concreto solo declara `_DEFAULT_BASE_URL` + `_PROVIDER_LABEL`.
- **Two-phase agent init** — pass 1 builds every agent draft; pass 3 wires delegation (the `delegate` tool), the scheduler tool, photos and Telegram tools once ALL agents exist. The runtimes are frozen last (pass 5): an `X | None` field means the capability is not configured for that agent, never that a pass is missing.
- **Delegación — subagente efímero con herencia contra el caller** — El pool de DEFINICIONES de sub-agentes es compartido, pero cada delegación NO usa el `run_agent_one_shot` pre-built del sub: construye una **instancia efímera one-shot resuelta contra el CALLER** vía `inaki.agents.wiring.build_ephemeral_child(definition_raw, caller_cfg=...)`. Resolución: `resolve_inherit(_deep_merge(SUBAGENT_DEFAULTS, definition_raw), parent_raw)` con `parent_raw` = config EFECTIVA del caller. El primitivo `inherit` (directiva de merge por bloque, resuelta en dicts crudos ANTES de pydantic y strippeada — NUNCA un campo de modelo) hace que el hijo herede del padre: `llm` por default (vía `SUBAGENT_DEFAULTS`), el resto opt-in. **Tools/recursos = SIEMPRE del caller** (`caller._tools`: workspace/memory/knowledge del padre); el sub recorta el subset visible con `tools.allowed` (filtro REQ-OS-5 en `RunAgentOneShotUseCase`, junto a la exclusión de `delegate` REQ-DG-9). El LLM se REUSA (misma instancia del caller) si la config llm efectiva coincide; si el sub la overridea → `LLMProviderFactory` con los `providers` heredados del caller. SIN embedder (el one-shot expone el toolkit completo sin RAG, REQ-OS-4). Misma def + caller P/Q distintos → instancias independientes heredando cada una de su padre. Ambos paths resuelven el efímero contra el caller: sync (`wire_delegation` arma el closure `build_child` con `get_sub_agent_raw` + `build_ephemeral_child`) y async (`BackgroundDelegationQueueAdapter`, `one_shot_resolver(caller_id, target_id)`). Scope: SOLO `delegate` — el carril de memoria (extractor/reconciliador) hereda por su cuenta vía `merged_llm_config`.
