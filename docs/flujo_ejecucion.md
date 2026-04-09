# Flujo de Ejecución — Iñaki v2

## Arranque del sistema

### Modo CLI (`python main.py [--agent id]`)

```
main.py
│
├── _bootstrap(config_dir)
│   ├── load_global_config(config_dir)
│   │   ├── _load_yaml_safe("config/global.yaml")
│   │   ├── _load_yaml_safe("config/global.secrets.yaml")
│   │   ├── _deep_merge(global, secrets) → merged_dict
│   │   └── return GlobalConfig, global_raw
│   │
│   ├── setup_logging(log_level)
│   │
│   └── AgentRegistry(config_dir, global_raw)
│       ├── glob("config/agents/*.yaml") — excluye .secrets y .example
│       ├── Para cada agente:
│       │   ├── load_agent_config(id, config_dir, global_raw)
│       │   │   ├── _load_yaml_safe("config/agents/{id}.yaml")
│       │   │   ├── _load_yaml_safe("config/agents/{id}.secrets.yaml")  [WARNING si no existe]
│       │   │   ├── _deep_merge(global_raw, agent_raw)
│       │   │   └── return AgentConfig(id, name, llm, embedding, memory, history, channels)
│       │   └── registry[id] = AgentConfig
│       └── log "N agente(s) cargado(s)"
│
├── AppContainer(global_config, registry)
│   └── Para cada AgentConfig en registry:
│       └── AgentContainer(agent_cfg, global_config)
│           ├── EmbeddingProviderFactory.create(cfg) → IEmbeddingProvider
│           ├── SQLiteMemoryRepository(db_path, embedder)
│           ├── LLMProviderFactory.create(cfg) → ILLMProvider
│           ├── YamlSkillRepository(skills_dir, embedder)
│           ├── SQLiteHistoryStore(history_cfg)
│           ├── ToolRegistry() + register(ShellTool, WebSearchTool)
│           ├── RunAgentUseCase(llm, memory, embedder, skills, history, tools, cfg)
│           └── ConsolidateMemoryUseCase(llm, memory, embedder, history, agent_id)
│
└── cli_runner.run(global_config, registry, agent_id)
    └── asyncio.run(run_cli(app, agent_id))
```

### Modo Daemon (`python main.py --daemon`)

```
main.py --daemon
│
├── _bootstrap(config_dir)      [igual que CLI]
│
├── AppContainer(global_config, registry)   [igual que CLI]
│
└── asyncio.run(run_daemon(app_container, registry))
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
    └── On shutdown: cancel all tasks → gather → log
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
AgentContainer.__init__
│
├── EmbeddingProviderFactory.create(cfg)
│   └── E5OnnxProvider(embedding_cfg)
│       └── _ensure_loaded() — carga model.onnx y tokenizer.json en primer uso (lazy)
│
├── SQLiteMemoryRepository(db_path, embedder)
│   └── _ensure_schema() — CREATE TABLE IF NOT EXISTS en primer uso (lazy)
│
├── LLMProviderFactory.create(cfg)
│   └── OpenRouterProvider(llm_cfg) — valida que api_key no sea None
│
├── YamlSkillRepository(skills_dir, embedder)
│   └── _ensure_loaded() — carga y embeds todos los YAML en primer uso (lazy)
│
├── SQLiteHistoryStore(history_cfg)
│   └── mkdir(parent de db_path) si no existe — schema creado lazy en primer uso
│
└── ToolRegistry()
    ├── register(ShellTool())
    └── register(WebSearchTool())
```

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
    archived   INTEGER NOT NULL DEFAULT 0
);
```

Solo se persisten filas con `role = "user"` o `"assistant"`. Los mensajes de tools son efímeros.

**Archive (soft-delete):** `UPDATE history SET archived = 1 WHERE agent_id = ? AND archived = 0`
Tras `consolidate`, las filas quedan con `archived=1` y luego se eliminan con `DELETE`.

---

## Comandos CLI disponibles

| Comando | Acción |
|---------|--------|
| `python main.py` | Chat CLI con agente por defecto |
| `python main.py --agent dev` | Chat CLI con agente 'dev' |
| `python main.py --agent list` | Lista todos los agentes |
| `python main.py --daemon` | Modo servicio (todos los canales) |
| `/consolidate` (en chat) | Extrae recuerdos y archiva historial |
| `/history` (en chat) | Muestra el historial actual |
| `/clear` (en chat) | Limpia historial sin archivar |
| `/agents` (en chat) | Lista agentes disponibles |
| `/help` (en chat) | Muestra ayuda |
| `/exit` o `/quit` | Sale |
