# Flujo de Ejecución — Inaki v2

## Arranque del sistema

### Modo CLI (`inaki [chat] [--agent id]`)

```
inaki (cli.py → app)
│
├── _bootstrap(config_dir, agents_dir)
│   ├── load_global_config(config_dir)
│   │   ├── _load_yaml_safe("~/.inaki/config/global.yaml")
│   │   ├── _load_yaml_safe("~/.inaki/config/global.secrets.yaml")
│   │   ├── _deep_merge(global, secrets) → merged_dict
│   │   └── return GlobalConfig, global_raw
│   │
│   ├── setup_logging(log_level)
│   │
│   └── AgentRegistry(agents_dir, global_raw)
│       ├── glob("~/.inaki/config/agents/*.yaml") — excluye .secrets y .example
│       ├── Para cada agente:
│       │   ├── load_agent_config(id, agents_dir, global_raw)
│       │   │   ├── _load_yaml_safe("~/.inaki/config/agents/{id}.yaml")
│       │   │   ├── _load_yaml_safe("~/.inaki/config/agents/{id}.secrets.yaml")  [WARNING si no existe]
│       │   │   ├── _deep_merge(global_raw, agent_raw)
│       │   │   └── return AgentConfig resuelto (4 capas mergeadas)
│       │   └── registry[id] = AgentConfig
│       └── log "N agente(s) cargado(s)"
│
├── AppContainer(global_config, registry)
│   ├── InMemoryScopeRegistryAdapter() — instancia ÚNICA compartida por todos los agentes
│   │
│   ├── Primera pasada — Para cada AgentConfig en registry:
│   │   └── AgentContainer(agent_cfg, global_config, scope_registry)
│   │       ├── EmbeddingProviderFactory.create(cfg) → IEmbeddingProvider
│   │       ├── SqliteEmbeddingCache(cache_filename)
│   │       ├── SQLiteMemoryRepository(db_filename, embedder)
│   │       ├── LLMProviderFactory.create(cfg) → ILLMProvider
│   │       ├── (si memory.llm difiere:) LLMProviderFactory separado para consolidación
│   │       ├── YamlSkillRepository(embedder, cache)
│   │       ├── SQLiteHistoryStore(history_cfg)
│   │       ├── ToolRegistry() + register(builtin tools)
│   │       ├── _register_extensions(ext_dirs) → tools, skills, knowledge_sources
│   │       ├── KnowledgeOrchestrator(sources) si knowledge habilitado
│   │       ├── (si photos habilitado:) vision + face_registry + scene_describer
│   │       ├── (si transcription configurado:) TranscriptionProviderFactory.create(cfg)
│   │       ├── RunAgentUseCase(llm, memory, embedder, skills, history, tools, cfg, ...)
│   │       ├── RunAgentOneShotUseCase(llm, tools, cfg)
│   │       └── ConsolidateMemoryUseCase(llm, memory, embedder, history, agent_id, memory_cfg)
│   │
│   ├── Segunda pasada — wire_delegation:
│   │   └── Registra tool `delegate` en cada container con refs a los demás
│   │       (los containers deben existir antes de las referencias cruzadas)
│   │
│   ├── Build enabled_agents = {id: container.consolidate_memory
│   │                           for each container where agent_config.memory.enabled}
│   ├── ConsolidateAllAgentsUseCase(enabled_agents, delay_seconds)
│   │
│   ├── LLMDispatcherAdapter(agents) — instancia ÚNICA compartida (lock-per-scope)
│   ├── BackgroundDelegationQueueAdapter(dispatcher, semaphore=3)
│   │
│   └── Scheduler wiring:
│       ├── SQLiteSchedulerRepo(scheduler_cfg.db_filename)
│       ├── ScheduleTaskUseCase(repo, on_mutation)
│       ├── SchedulerDispatchPorts(
│       │       channel_router=ChannelRouter(native_sinks, fallback_cfg),
│       │       llm_dispatcher=LLMDispatcherAdapter (misma instancia),
│       │       consolidator=ConsolidationDispatchAdapter(consolidate_all_agents),
│       │       http_caller=HttpCallerAdapter())
│       └── SchedulerService(repo, dispatch_ports, scheduler_cfg)
│
└── cli_runner.run(global_config, registry, agent_id)
    └── asyncio.run(run_cli(app, agent_id))
```

### Modo Daemon (`inaki daemon`)

```
inaki daemon
│
├── _bootstrap(config_dir, agents_dir)      [igual que CLI]
│
├── AppContainer(global_config, registry)   [igual que CLI]
│
└── asyncio.run(run_daemon(app_container, registry))
    │
    ├── app_container.startup()
    │   ├── _reconcile_consolidate_memory_task()   [ver sección abajo]
    │   └── scheduler_service.start()
    │       ├── repo.ensure_schema()
    │       ├── _handle_missed_on_startup()
    │       └── create_task(_loop())  ← loop principal del scheduler
    │
    ├── Registrar SIGTERM/SIGINT → shutdown_event.set()
    │
    ├── Para cada agente con canal 'rest':
    │   └── asyncio.create_task(_run_rest_server(agent_cfg, container))
    │       └── uvicorn.Server(FastAPI app, host, port).serve()
    │
    ├── Para cada agente con canal 'telegram':
    │   └── asyncio.create_task(_run_telegram_bot(agent_cfg, container))
    │       └── async with bot._app:
    │               await app.start()
    │               await app.updater.start_polling()
    │               await asyncio.Event().wait()  # forever
    │
    ├── asyncio.wait([*tasks, shutdown_task], FIRST_COMPLETED)
    │
    └── On shutdown: app_container.shutdown() → cancel tasks → gather → log
```

### Modo Consolidación one-shot (`inaki consolidate [--agent id]`)

```
inaki consolidate
│
├── _bootstrap(config_dir, agents_dir)
├── AppContainer(global_config, registry)   ← NO arranca scheduler ni canales
│
└── _run_consolidate(global_config, registry, agent)
    │
    ├── Con --agent X:
    │   ├── container = app.get_agent("X")
    │   ├── await container.consolidate_memory.execute()
    │   │   └── consolida solo X (ignora memory.enabled)
    │   └── print(f"X: {result}")
    │
    └── Sin --agent:
        └── await app.consolidate_all_agents.execute()
            ├── itera enabled_agents
            ├── para cada uno: await uc.execute() + asyncio.sleep(delay_seconds)
            └── print(resumen con ✓/✗ por agente)
```

---

## Descubrimiento dinámico de providers

### LLMProviderFactory (`infrastructure/factories/llm_factory.py`)

```
LLMProviderFactory.create(agent_cfg)
│
├── _load()  [solo la primera vez — cache en _registry]
│   ├── pkgutil.iter_modules("adapters/outbound/providers/")
│   ├── Para cada módulo (excepto "base"):
│   │   ├── importlib.import_module(...)
│   │   ├── leer PROVIDER_NAME del módulo
│   │   └── encontrar clase que hereda BaseLLMProvider → registry[PROVIDER_NAME] = clase
│   └── log "providers disponibles: [openrouter, ollama, openai, groq]"
│
└── return registry[cfg.llm.provider](cfg.llm)
    # Ej: registry["openrouter"](llm_config) → OpenRouterProvider instance
```

Mismo mecanismo para `EmbeddingProviderFactory` apuntando a `adapters/outbound/embedding/`.

---

## Ciclo de vida de un AgentContainer

```
AgentContainer.__init__(agent_config, global_config, scope_registry)
│
├── EmbeddingProviderFactory.create(cfg) → IEmbeddingProvider
│   └── E5OnnxProvider(embedding_cfg)
│       └── _ensure_loaded() — carga model.onnx y tokenizer.json en primer uso (lazy)
│
├── SqliteEmbeddingCache(cache_filename) → IEmbeddingCache
│
├── SQLiteMemoryRepository(db_filename, embedder)
│   └── _ensure_schema() — CREATE TABLE IF NOT EXISTS en primer uso (lazy)
│
├── LLMProviderFactory.create(cfg) → ILLMProvider
│   └── Provider según cfg.llm.provider (openrouter, groq, ollama, openai, deepseek)
│
├── (si memory.llm difiere de llm:) LLMProviderFactory separado para consolidación
│
├── YamlSkillRepository(embedder, cache)
│   └── _ensure_loaded() — carga y embeds los YAML registrados vía add_file() en primer uso (lazy)
│
├── SQLiteHistoryStore(history_cfg)
│   └── _ensure_schema() — migración automática de columnas si schema legacy
│
├── ToolRegistry() + register(builtin tools: web_search, read_file, write_file,
│                              patch_file, edit_file, scheduler, memory_tools,
│                              knowledge_search, face_tools)
│
├── _register_extensions(ext_dirs) → tools, skills, knowledge_sources adicionales
│
├── KnowledgeOrchestrator(sources) si knowledge habilitado
│
├── (si photos habilitado:)
│   ├── InsightFaceAdapter (lazy-load en primera foto, ~400MB RAM)
│   ├── SqliteFaceRegistry(faces.db)
│   └── SceneDescriber (anthropic/openai/groq)
│
├── (si transcription configurado:)
│   └── TranscriptionProviderFactory.create(cfg) → ITranscriptionProvider
│
├── RunAgentUseCase(llm, memory, embedder, skills, history, tools, cfg, knowledge, ...)
├── RunAgentOneShotUseCase(llm, tools, cfg) — para el scheduler (sin historial)
└── ConsolidateMemoryUseCase(llm/memory_llm, memory, embedder, history, agent_id, memory_cfg)
```

**Nota:** la tool `delegate` NO se registra en `__init__` — se conecta en la segunda pasada
de `AppContainer` vía `wire_delegation()`, porque necesita que TODOS los containers ya existan.

---

## Formato del historial en SQLite

**Base de datos:** `data/history.db` (separada de `data/inaki.db` que usa sqlite-vec)

**Tabla `history`:**

```sql
CREATE TABLE history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT    NOT NULL,
    role       TEXT    NOT NULL,       -- "user" | "assistant"
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,       -- ISO8601 UTC: "2026-04-09T15:30:00+00:00"
    archived   INTEGER NOT NULL DEFAULT 0,  -- LEGACY, siempre 0 (ver nota)
    infused    INTEGER NOT NULL DEFAULT 0   -- 0=pendiente de extracción, 1=ya procesado
);
```

Solo se persisten filas con `role = "user"` o `"assistant"`. Los mensajes de tools son efímeros.

**Nota sobre `archived`:** esta columna es legacy. En versiones anteriores se
usaba para un soft-delete (archive → clear). El flujo nuevo usa `trim` (DELETE
puro con `NOT IN (SELECT ... LIMIT N)`), así que ninguna query de
`SQLiteHistoryStore` lee ni escribe `archived` — siempre queda en 0. La
columna se mantiene en `CREATE TABLE IF NOT EXISTS` para compatibilidad con
DBs existentes.

**Nota sobre `infused`:** gate contra reprocesamiento. Un mensaje con
`infused=0` está pendiente de extracción por el `ConsolidateMemoryUseCase`;
con `infused=1` ya fue procesado en una corrida previa. El UC filtra por
`infused=0` (`load_uninfused`) al cargar, y marca todos como `infused=1`
tras persistir los recuerdos. Los mensajes que sobreviven al trim
(`keep_last`) quedan con `infused=1`, evitando duplicados en la próxima
consolidación. DBs preexistentes migran automáticamente vía `ALTER TABLE
ADD COLUMN` + `UPDATE ... SET infused = 1` en el primer `_ensure_schema`.

**Trim (tras consolidación exitosa):**
```sql
DELETE FROM history
WHERE agent_id = ?
  AND id NOT IN (
    SELECT id FROM history
    WHERE agent_id = ?
    ORDER BY id DESC
    LIMIT ?
  );
```
Preserva los últimos N mensajes del agente (N = `memory.keep_last_messages`
resuelto, con sentinel `0 → 84`). Transaccional: solo se ejecuta tras
extracción + persistencia exitosas.

**Clear (slash `/clear` y `ConsolidateAllAgentsUseCase` — NO usado por trim):**
```sql
DELETE FROM history WHERE agent_id = ?;
```
Wipe total para el agente.

---

## Comandos CLI disponibles

| Comando | Acción |
|---------|--------|
| `inaki` | Chat CLI con agente por defecto |
| `inaki chat --agent dev` | Chat CLI con agente 'dev' |
| `inaki chat --agent list` | Lista todos los agentes |
| `inaki daemon` | Modo servicio (todos los canales + scheduler) |
| `inaki consolidate` | Consolida todos los agentes habilitados con delay y sale |
| `inaki consolidate --agent dev` | Consolida solo el agente indicado y sale |
| `inaki inspect "msg"` | Inspecciona el pipeline de prompt (routing + memoria) sin llamar al LLM |
| `inaki setup` | TUI interactiva de configuración (offline) |
| `inaki reload` | Hot-reload del daemon |
| `/consolidate` (en chat) | Extrae recuerdos y archiva historial del agente actual |
| `/history` (en chat) | Muestra el historial actual |
| `/clear` (en chat) | Limpia historial sin archivar |
| `/agents` (en chat) | Lista agentes disponibles |
| `/help` (en chat) | Muestra ayuda |
| `/exit` o `/quit` | Sale |

---

## Flujo de consolidación de memoria

La memoria a largo plazo se alimenta de dos formas: manual (`/consolidate` en
chat o `inaki consolidate` por CLI) y automática (tarea programada nocturna).
Ambos caminos terminan invocando el mismo use case per-agente.

### Reconciliación de la tarea builtin al arrancar

```
AppContainer.startup()
│
└── _reconcile_consolidate_memory_task()
    │
    ├── target_schedule ← global_config.memory.schedule
    ├── existing ← scheduler_repo.get_task(CONSOLIDATE_MEMORY_TASK_ID)  # id=1
    │
    ├── Si existing is None:
    │   ├── task ← build_consolidate_memory_task(target_schedule)
    │   └── scheduler_repo.seed_builtin(task)
    │       └── seed_builtin computa next_run con croniter si viene None
    │
    └── Si existing existe:
        ├── Si existing.schedule != target_schedule:
        │   ├── new_schedule ← target_schedule
        │   ├── new_next_run ← croniter(target_schedule, now).get_next()
        │   └── needs_save = True
        │
        ├── Si existing.status == FAILED:
        │   ├── new_status ← PENDING
        │   ├── new_retry_count ← 0
        │   ├── Si new_next_run is None or pasado:
        │   │   └── new_next_run ← croniter(new_schedule, now).get_next()
        │   └── needs_save = True
        │
        ├── Si existing.next_run is None:
        │   ├── new_next_run ← croniter(new_schedule, now).get_next()
        │   └── needs_save = True
        │
        └── Si needs_save:
            └── scheduler_repo.save_task(existing.model_copy(update={...}))
```

### Disparo automático (nocturno)

```
SchedulerService._loop()  [corriendo desde startup()]
│
├── next_task ← repo.get_next_due(now)
│   └── SELECT * WHERE enabled=1 AND status='pending' ORDER BY next_run ASC
│
├── Si next_task.next_run > now:
│   └── await wake_event.wait(timeout=min(wait_secs, 60))
│
└── Si es hora: _execute_task(next_task)
    │
    └── _dispatch_trigger(task)
        │
        └── isinstance(payload, ConsolidateMemoryPayload):
            │
            └── return await dispatch.consolidator.consolidate_all()
                │
                └── ConsolidateAllAgentsUseCase.execute()
                    │
                    ├── Para cada agent_id en enabled_agents:
                    │   ├── logger.info("Consolidando '%s'...", agent_id)
                    │   ├── try:
                    │   │   └── await uc.execute()  ← ConsolidateMemoryUseCase
                    │   ├── except ConsolidationError:
                    │   │   └── result.failed[agent_id] = str(exc)
                    │   ├── except Exception:
                    │   │   └── result.failed[agent_id] = "error inesperado: ..."
                    │   └── Si no es el último: await asyncio.sleep(delay_seconds)
                    │
                    └── return result.format()  ← mensaje con ✓/✗ por agente
```

### Per-agente: `ConsolidateMemoryUseCase.execute()`

```
ConsolidateMemoryUseCase.execute()
│
├── messages ← history.load_uninfused(agent_id)
│   # SELECT WHERE agent_id = ? AND infused = 0 ORDER BY id ASC
│   └── Si vacío: return "No hay mensajes nuevos para consolidar."
│       (NO-OP IDEMPOTENTE — no se toca nada)
│
├── history_text ← formato "role [ts]: content" por cada mensaje
├── prompt ← _EXTRACTOR_PROMPT_TEMPLATE.format(history=history_text)
│
├── raw_json ← llm.complete(messages=[], system_prompt=prompt)
│   └── except → raise ConsolidationError (NO mark, NO trim)
│
├── facts ← _parse_facts(raw_json)
│   └── except → raise ConsolidationError (NO mark, NO trim)
│
├── Filtro por min_relevance_score:
│   └── facts ← [f for f in facts if f.relevance >= threshold]
│
├── Para cada fact filtrado:
│   ├── embedding ← embedder.embed_passage(fact.content)
│   ├── entry ← MemoryEntry(..., agent_id=self._agent_id)  # atribución
│   ├── memory.store(entry)
│   └── except → raise ConsolidationError (NO mark, NO trim, rollback parcial)
│
├── history.mark_infused(agent_id)   ← GATE
│   # UPDATE SET infused = 1 WHERE agent_id = ? AND infused = 0
│   # Cierra el gate ANTES del trim: los mensajes que sobrevivan a trim
│   # (keep_last) quedan marcados, la próxima corrida no los reprocesa.
│   └── except → raise ConsolidationError (NO trim)
│
├── _write_digest()  ← best-effort, no aborta si falla
│   └── memory.get_recent(digest_size) → markdown → disk
│
└── Si todo OK:
    ├── keep_last ← memory_cfg.resolved_keep_last_messages()  # 0 → 84
    ├── history.trim(agent_id, keep_last=keep_last)
    │   # DELETE WHERE id NOT IN (SELECT id ... LIMIT keep_last)
    │   # Preserva los últimos N mensajes (todos con infused=1) como
    │   # contexto inmediato para el próximo turno
    └── return f"✓ {stored} recuerdo(s) extraído(s). Historial truncado (últimos {keep_last} mensajes preservados)."
```

**Transaccionalidad:** si cualquier paso del LLM, parseo, embedding,
persistencia o `mark_infused` falla, `history.trim` NO se llama. El
historial queda intacto y la próxima corrida reintenta el mismo contenido.

**Gate de reprocesamiento:** `mark_infused` se llama ANTES de `trim` para
cerrar el gate mientras los mensajes todavía existen. Tras el trim, los N
supervivientes quedan como `infused=1` y la próxima consolidación los
ignora (vía `load_uninfused`), procesando solo los mensajes NUEVOS añadidos
entre consolidación y consolidación.

**Idempotencia:** ejecutar el UC dos veces seguidas es seguro — la segunda
corrida ve `load_uninfused → []` y retorna sin tocar nada.
