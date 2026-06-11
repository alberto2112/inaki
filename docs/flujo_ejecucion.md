# Execution Flow — Inaki v2

## System Startup

### CLI Mode (`inaki [chat] [--agent id]`)

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
│       ├── glob("~/.inaki/config/agents/*.yaml") — excludes .secrets and .example
│       ├── For each agent:
│       │   ├── load_agent_config(id, agents_dir, global_raw)
│       │   │   ├── _load_yaml_safe("~/.inaki/config/agents/{id}.yaml")
│       │   │   ├── _load_yaml_safe("~/.inaki/config/agents/{id}.secrets.yaml")  [WARNING if missing]
│       │   │   ├── _deep_merge(global_raw, agent_raw)
│       │   │   └── return resolved AgentConfig (4 layers merged)
│       │   └── registry[id] = AgentConfig
│       └── log "N agent(s) loaded"
│
├── AppContainer(global_config, registry)
│   ├── InMemoryScopeRegistryAdapter() — SINGLE shared instance across all agents
│   │
│   ├── First pass — For each AgentConfig in registry:
│   │   └── AgentContainer(agent_cfg, global_config, scope_registry)
│   │       ├── EmbeddingProviderFactory.create(cfg) → IEmbeddingProvider
│   │       ├── SqliteEmbeddingCache(cache_filename)
│   │       ├── SQLiteMemoryRepository(db_filename, embedder)
│   │       ├── LLMProviderFactory.create(cfg) → ILLMProvider
│   │       ├── (if memory.llm differs:) separate LLMProviderFactory for consolidation
│   │       ├── YamlSkillRepository(embedder, cache)
│   │       ├── SQLiteHistoryStore(history_cfg)
│   │       ├── ToolRegistry() + register(builtin tools)
│   │       ├── _register_extensions(ext_dirs) → tools, skills, knowledge_sources
│   │       ├── KnowledgeOrchestrator(sources) if knowledge enabled
│   │       ├── (if photos enabled:) vision + face_registry + scene_describer
│   │       ├── (if transcription configured:) TranscriptionProviderFactory.create(cfg)
│   │       ├── RunAgentUseCase(llm, memory, ..., settings=build_run_agent_settings(cfg))
│   │       ├── RunAgentOneShotUseCase(llm, tools, settings=OneShotSettings(...))
│   │       └── ConsolidateMemoryUseCase(llm, memory, embedder, history, agent_id,
│   │                                    settings=build_memory_settings(cfg.memory))
│   │
│   ├── Second pass — wire_delegation:
│   │   └── Registers `delegate` tool in each container with refs to the others
│   │       (containers must exist before cross-references)
│   │
│   ├── Build enabled_agents = {id: container.consolidate_memory
│   │                           for each container where agent_config.memory.enabled}
│   ├── ConsolidateAllAgentsUseCase(enabled_agents, delay_seconds)
│   │
│   ├── LLMDispatcherAdapter(agents) — SINGLE shared instance (lock-per-scope)
│   ├── BackgroundDelegationQueueAdapter(dispatcher, semaphore=3)
│   │
│   └── Scheduler wiring:
│       ├── SQLiteSchedulerRepo(scheduler_cfg.db_filename)
│       ├── ScheduleTaskUseCase(repo, on_mutation)
│       ├── SchedulerDispatchPorts(
│       │       channel_router=ChannelRouter(native_sinks, fallback_cfg),
│       │       llm_dispatcher=LLMDispatcherAdapter (same instance),
│       │       consolidator=ConsolidationDispatchAdapter(consolidate_all_agents),
│       │       http_caller=HttpCallerAdapter())
│       └── SchedulerService(repo, dispatch_ports, scheduler_cfg)
│
└── cli_runner.run(global_config, registry, agent_id)
    └── asyncio.run(run_cli(app, agent_id))
```

### Daemon Mode (`inaki daemon`)

```
inaki daemon
│
├── _bootstrap(config_dir, agents_dir)      [same as CLI]
│
├── AppContainer(global_config, registry)   [same as CLI]
│
└── asyncio.run(run_daemon(app_container, registry))
    │
    ├── app_container.startup()
    │   ├── _reconcile_consolidate_memory_task()   [see section below]
    │   └── scheduler_service.start()
    │       ├── repo.ensure_schema()
    │       ├── _handle_missed_on_startup()
    │       └── create_task(_loop())  ← scheduler main loop
    │
    ├── Register SIGTERM/SIGINT → shutdown_event.set()
    │
    ├── Admin server (única superficie HTTP, ruteo por agent_id):
    │   └── asyncio.create_task(_run_admin_server(app_container, admin_cfg))
    │       └── uvicorn.Server(create_admin_app(), admin.host, admin.port).serve()
    │
    ├── For each agent with 'telegram' channel:
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

### One-shot Consolidation Mode (`inaki consolidate [--agent id]`)

```
inaki consolidate
│
├── _bootstrap(config_dir, agents_dir)
├── AppContainer(global_config, registry)   ← does NOT start scheduler or channels
│
└── _run_consolidate(global_config, registry, agent)
    │
    ├── With --agent X:
    │   ├── container = app.get_agent("X")
    │   ├── await container.consolidate_memory.execute()
    │   │   └── consolidates only X (ignores memory.enabled)
    │   └── print(f"X: {result}")
    │
    └── Without --agent:
        └── await app.consolidate_all_agents.execute()
            ├── iterates enabled_agents
            ├── for each: await uc.execute() + asyncio.sleep(delay_seconds)
            └── print(summary with ✓/✗ per agent)
```

---

## Dynamic Provider Discovery

### LLMProviderFactory (`infrastructure/factories/llm_factory.py`)

```
LLMProviderFactory.create(agent_cfg)
│
├── _load()  [first time only — cached in _registry]
│   ├── pkgutil.iter_modules("adapters/outbound/providers/")
│   ├── For each module (except "base"):
│   │   ├── importlib.import_module(...)
│   │   ├── read PROVIDER_NAME from the module
│   │   └── find class inheriting BaseLLMProvider → registry[PROVIDER_NAME] = class
│   └── log "available providers: [openrouter, ollama, openai, groq]"
│
└── return registry[cfg.llm.provider](cfg.llm)
    # E.g.: registry["openrouter"](llm_config) → OpenRouterProvider instance
```

Same mechanism for `EmbeddingProviderFactory` pointing to `adapters/outbound/embedding/`.

---

## AgentContainer Lifecycle

```
AgentContainer.__init__(agent_config, global_config, scope_registry)
│
├── EmbeddingProviderFactory.create(cfg) → IEmbeddingProvider
│   └── E5OnnxProvider(embedding_cfg)
│       └── _ensure_loaded() — loads model.onnx and tokenizer.json on first use (lazy)
│
├── SqliteEmbeddingCache(cache_filename) → IEmbeddingCache
│
├── SQLiteMemoryRepository(db_filename, embedder)
│   └── _ensure_schema() — CREATE TABLE IF NOT EXISTS on first use (lazy)
│
├── LLMProviderFactory.create(cfg) → ILLMProvider
│   └── Provider based on cfg.llm.provider (openrouter, groq, ollama, openai, deepseek)
│
├── (if memory.llm differs from llm:) separate LLMProviderFactory for consolidation
│
├── YamlSkillRepository(embedder, cache)
│   └── _ensure_loaded() — loads and embeds YAMLs registered via add_file() on first use (lazy)
│
├── SQLiteHistoryStore(history_cfg)
│   └── _ensure_schema() — automatic column migration if legacy schema
│
├── ToolRegistry() + register(builtin tools: web_search, read_file, write_file,
│                              patch_file, edit_file, scheduler, memory_tools,
│                              knowledge_search, face_tools)
│
├── _register_extensions(ext_dirs) → additional tools, skills, knowledge_sources
│
├── KnowledgeOrchestrator(sources) if knowledge enabled
│
├── (if photos enabled:)
│   ├── InsightFaceAdapter (lazy-load on first photo, ~400MB RAM)
│   ├── SqliteFaceRegistry(faces.db)
│   └── SceneDescriber (anthropic/openai/groq)
│
├── (if transcription configured:)
│   └── TranscriptionProviderFactory.create(cfg) → ITranscriptionProvider
│
├── RunAgentUseCase(llm, memory, embedder, skills, history, tools,
│                   settings=build_run_agent_settings(cfg), knowledge, ...)
├── RunAgentOneShotUseCase(llm, tools, settings=OneShotSettings(...)) — scheduler (no history)
└── ConsolidateMemoryUseCase(llm/memory_llm, memory, embedder, history, agent_id,
                             settings=build_memory_settings(cfg.memory))
```

**Note:** use cases never receive `AgentConfig` — each one declares its parameters
as a frozen settings VO (`core/domain/value_objects/agent_settings.py`); the
config→VO mapping lives only in the `build_*_settings` builders of `container.py`.

**Note:** the `delegate` tool is NOT registered in `__init__` — it is wired in the second pass
of `AppContainer` via `wire_delegation()`, because it needs ALL containers to already exist.

---

## History Format in SQLite

**Database:** `data/history.db` (separate from `data/inaki.db` which uses sqlite-vec)

**`history` table:**

```sql
CREATE TABLE history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT    NOT NULL,
    role       TEXT    NOT NULL,       -- "user" | "assistant"
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,       -- ISO8601 UTC: "2026-04-09T15:30:00+00:00"
    archived   INTEGER NOT NULL DEFAULT 0,  -- LEGACY, always 0 (see note)
    infused    INTEGER NOT NULL DEFAULT 0   -- 0=pending extraction, 1=already processed
);
```

Only rows with `role = "user"` or `"assistant"` are persisted. Tool messages are ephemeral.

**Note on `archived`:** this column is legacy. In previous versions it was
used for a soft-delete (archive → clear). The new flow uses `trim` (pure DELETE
with `NOT IN (SELECT ... LIMIT N)`), so no query in
`SQLiteHistoryStore` reads or writes `archived` — it always stays at 0. The
column is kept in `CREATE TABLE IF NOT EXISTS` for compatibility with
existing DBs.

**Note on `infused`:** gate against reprocessing. A message with
`infused=0` is pending extraction by `ConsolidateMemoryUseCase`;
with `infused=1` it was already processed in a previous run. The UC filters by
`infused=0` (`load_uninfused`) when loading, and marks all as `infused=1`
after persisting memories. Messages that survive the trim
(`keep_last`) end up with `infused=1`, preventing duplicates in the next
consolidation. Pre-existing DBs are migrated automatically via `ALTER TABLE
ADD COLUMN` + `UPDATE ... SET infused = 1` on the first `_ensure_schema`.

**Trim (after successful consolidation):**
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
Preserves the last N messages for the agent (N = resolved `memory.keep_last_messages`,
with sentinel `0 → 84`). Transactional: only runs after successful
extraction + persistence.

**Clear (slash `/clear` and `ConsolidateAllAgentsUseCase` — NOT used by trim):**
```sql
DELETE FROM history WHERE agent_id = ?;
```
Full wipe for the agent.

---

## Available CLI Commands

| Command | Action |
|---------|--------|
| `inaki` | CLI chat with the default agent |
| `inaki chat --agent dev` | CLI chat with the 'dev' agent |
| `inaki chat --agent list` | Lists all agents |
| `inaki daemon` | Service mode (all channels + scheduler) |
| `inaki consolidate` | Consolidates all enabled agents with delay and exits |
| `inaki consolidate --agent dev` | Consolidates only the specified agent and exits |
| `inaki inspect "msg"` | Inspects the prompt pipeline (routing + memory) without calling the LLM |
| `inaki setup` | Interactive configuration TUI (offline) |
| `inaki reload` | Hot-reload the daemon |
| `/consolidate` (in chat) | Extracts memories and archives the current agent's history |
| `/history` (in chat) | Shows the current history |
| `/clear` (in chat) | Clears history without archiving |
| `/agents` (in chat) | Lists available agents |
| `/help` (in chat) | Shows help |
| `/exit` or `/quit` | Exits |

---

## Memory Consolidation Flow

Long-term memory is fed in two ways: manual (`/consolidate` in
chat or `inaki consolidate` via CLI) and automatic (nightly scheduled task).
Both paths end up invoking the same per-agent use case.

### Builtin Task Reconciliation at Startup

```
AppContainer.startup()
│
└── _reconcile_consolidate_memory_task()
    │
    ├── target_schedule ← global_config.memory.schedule
    ├── existing ← scheduler_repo.get_task(CONSOLIDATE_MEMORY_TASK_ID)  # id=1
    │
    ├── If existing is None:
    │   ├── task ← build_consolidate_memory_task(target_schedule)
    │   └── scheduler_repo.seed_builtin(task)
    │       └── seed_builtin computes next_run with croniter if it comes as None
    │
    └── If existing exists:
        ├── If existing.schedule != target_schedule:
        │   ├── new_schedule ← target_schedule
        │   ├── new_next_run ← croniter(target_schedule, now).get_next()
        │   └── needs_save = True
        │
        ├── If existing.status == FAILED:
        │   ├── new_status ← PENDING
        │   ├── new_retry_count ← 0
        │   ├── If new_next_run is None or in the past:
        │   │   └── new_next_run ← croniter(new_schedule, now).get_next()
        │   └── needs_save = True
        │
        ├── If existing.next_run is None:
        │   ├── new_next_run ← croniter(new_schedule, now).get_next()
        │   └── needs_save = True
        │
        └── If needs_save:
            └── scheduler_repo.save_task(existing.model_copy(update={...}))
```

### Automatic Trigger (nightly)

```
SchedulerService._loop()  [running since startup()]
│
├── next_task ← repo.get_next_due(now)
│   └── SELECT * WHERE enabled=1 AND status='pending' ORDER BY next_run ASC
│
├── If next_task.next_run > now:
│   └── await wake_event.wait(timeout=min(wait_secs, 60))
│
└── If it's time: _execute_task(next_task)
    │
    └── _dispatch_trigger(task)
        │
        └── isinstance(payload, ConsolidateMemoryPayload):
            │
            └── return await dispatch.consolidator.consolidate_all()
                │
                └── ConsolidateAllAgentsUseCase.execute()
                    │
                    ├── For each agent_id in enabled_agents:
                    │   ├── logger.info("Consolidating '%s'...", agent_id)
                    │   ├── try:
                    │   │   └── await uc.execute()  ← ConsolidateMemoryUseCase
                    │   ├── except ConsolidationError:
                    │   │   └── result.failed[agent_id] = str(exc)
                    │   ├── except Exception:
                    │   │   └── result.failed[agent_id] = "unexpected error: ..."
                    │   └── If not the last: await asyncio.sleep(delay_seconds)
                    │
                    └── return result.format()  ← message with ✓/✗ per agent
```

### Per-agent: `ConsolidateMemoryUseCase.execute()`

```
ConsolidateMemoryUseCase.execute()
│
├── messages ← history.load_uninfused(agent_id)
│   # SELECT WHERE agent_id = ? AND infused = 0 ORDER BY id ASC
│   └── If empty: return "No new messages to consolidate."
│       (NO-OP IDEMPOTENT — nothing is touched)
│
├── history_text ← "role [ts]: content" format for each message
├── prompt ← _EXTRACTOR_PROMPT_TEMPLATE.format(history=history_text)
│
├── raw_json ← llm.complete(messages=[], system_prompt=prompt)
│   └── except → raise ConsolidationError (NO mark, NO trim)
│
├── facts ← _parse_facts(raw_json)
│   └── except → raise ConsolidationError (NO mark, NO trim)
│
├── Filter by min_relevance_score:
│   └── facts ← [f for f in facts if f.relevance >= threshold]
│
├── For each filtered fact:
│   ├── embedding ← embedder.embed_passage(fact.content)
│   ├── entry ← MemoryEntry(..., agent_id=self._agent_id)  # attribution
│   ├── memory.store(entry)
│   └── except → raise ConsolidationError (NO mark, NO trim, partial rollback)
│
├── history.mark_infused(agent_id)   ← GATE
│   # UPDATE SET infused = 1 WHERE agent_id = ? AND infused = 0
│   # Closes the gate BEFORE the trim: messages that survive the trim
│   # (keep_last) end up marked, the next run won't reprocess them.
│   └── except → raise ConsolidationError (NO trim)
│
├── _write_digest()  ← best-effort, does not abort on failure
│   └── memory.get_recent(digest_size) → markdown → disk
│
└── If everything OK:
    ├── keep_last ← memory_cfg.resolved_keep_last_messages()  # 0 → 84
    ├── history.trim(agent_id, keep_last=keep_last)
    │   # DELETE WHERE id NOT IN (SELECT id ... LIMIT keep_last)
    │   # Preserves the last N messages (all with infused=1) as
    │   # immediate context for the next turn
    └── return f"✓ {stored} memory(ies) extracted. History trimmed (last {keep_last} messages preserved)."
```

**Transactionality:** if any step in the LLM call, parsing, embedding,
persistence, or `mark_infused` fails, `history.trim` is NOT called. The
history remains intact and the next run retries the same content.

**Reprocessing gate:** `mark_infused` is called BEFORE `trim` to
close the gate while the messages still exist. After the trim, the N
survivors end up as `infused=1` and the next consolidation ignores
them (via `load_uninfused`), processing only NEW messages added
between consolidation runs.

**Idempotency:** running the UC twice in a row is safe — the second
run sees `load_uninfused → []` and returns without touching anything.
