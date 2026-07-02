# Inaki — Technical Specification

> Reference document for the development of the Inaki agent.  
> Reflects the actual state of the system in v2.x.

---

## 1. Overview

Inaki is a personal agentic AI assistant designed to run as a systemd service on a **Raspberry Pi 5 (4 GB RAM, ARM64)**. The project follows a **strict hexagonal architecture (Ports & Adapters)** to ensure modularity, testability, and extensibility.

### Design Principles

- **The core knows nothing about the outside world.** No file in `core/` imports from `adapters/` or infrastructure libraries. Only stdlib + `core/` types allowed.
- **Inviolable dependency direction:** `adapters/` → `core/`. Never reversed.
- **Single wiring point:** `infrastructure/container.py` is the only place where concrete adapters are instantiated.
- **Configuration in `~/.inaki/`:** all user data (configs, secrets, DBs, models) lives outside the repo.
- **Designed for the Pi 5:** RAM footprint, ARM64, and token cost are first-class constraints.

---

## 2. Technology Stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| Target hardware | Raspberry Pi 5, 4 GB RAM, ARM64 |
| Deployment | systemd service |
| LLM providers | OpenRouter, OpenAI, Groq, Ollama, DeepSeek (dynamic discovery) |
| Embeddings | `multilingual-e5-small` (ONNX) · OpenAI (alternative) |
| Vector store | `sqlite-vec` + SQLite3 |
| History | SQLite3 (`aiosqlite`) |
| Config | YAML · 4-layer merge · `pydantic` v2 |
| Tests | `pytest` + `pytest-asyncio` (`auto` mode) |
| CLI | `typer` + `rich` |
| Config TUI | `textual` + `ruamel.yaml` |
| Inbound Telegram | `python-telegram-bot` v21+ (async) |
| Inbound REST | `FastAPI` + `uvicorn` |
| HTTP client | `httpx` (async) |
| Face recognition | `InsightFace` (lazy-loaded, ~400 MB RAM) |
| Scheduler | `croniter` |
| Voice transcription | Whisper via Groq API |

---

## 3. Directory Structure

```
inaki/                                  ← repository root
│
├── core/                               ← Hexagon: zero external dependencies
│   ├── domain/
│   │   ├── entities/
│   │   │   ├── message.py              # Message, Role
│   │   │   ├── memory.py              # MemoryEntry
│   │   │   ├── skill.py               # Skill, SkillResult
│   │   │   ├── task.py                # ScheduledTask, TaskStatus, TaskType
│   │   │   ├── task_log.py            # TaskLog (execution history)
│   │   │   ├── face.py                # FaceDetection, Person, KnownFace
│   │   │   └── background_task.py     # BackgroundTaskView (async delegation)
│   │   ├── value_objects/
│   │   │   ├── agent_context.py       # AgentContext → build_system_prompt()
│   │   │   ├── agent_info.py          # AgentInfoDTO
│   │   │   ├── agent_settings.py      # Settings VOs per use case (Run/OneShot/Memory/Photos)
│   │   │   ├── attachment.py          # IncomingAttachment + @-attachment grammar (single source)
│   │   │   ├── channel_context.py     # ChannelContext + ContextVar per-turn
│   │   │   ├── chat_turn_result.py    # ChatTurnResult
│   │   │   ├── conversation_state.py  # ConversationState
│   │   │   ├── delegation_result.py   # DelegationResult
│   │   │   ├── dispatch_result.py     # DispatchResult
│   │   │   ├── embedding.py           # Embedding(vector, model)
│   │   │   ├── knowledge_chunk.py     # KnowledgeChunk (RAG)
│   │   │   ├── llm_response.py        # LLMResponse (text + tool_calls)
│   │   │   └── telegram_file.py       # TelegramFileRecord (transport metadata, file_id)
│   │   ├── services/
│   │   │   ├── scheduler_service.py   # SchedulerService (cron loop)
│   │   │   ├── knowledge_orchestrator.py  # Multi-source RAG
│   │   │   ├── sticky_selector.py     # Sticky semantic routing (TTL)
│   │   │   ├── rate_limiter.py        # FixedWindowRateLimiter
│   │   │   ├── broadcast_buffer.py    # Group message buffer
│   │   │   ├── prepend_timestamps.py  # Injects timestamps into history
│   │   │   └── similarity.py          # Cosine similarity utils
│   │   ├── utils/
│   │   │   └── time_parser.py         # Time expression parser (ONESHOT)
│   │   └── errors.py                  # InakiError and subclasses
│   │
│   ├── ports/
│   │   ├── config_repository.py       # IConfigRepository (YAML read/write)
│   │   ├── inbound/
│   │   │   ├── agent_port.py          # IAgentUseCase
│   │   │   └── scheduler_port.py      # ISchedulerUseCase
│   │   └── outbound/
│   │       ├── llm_port.py            # ILLMProvider
│   │       ├── llm_dispatcher_port.py # ILLMDispatcher (scoped full turn)
│   │       ├── memory_port.py         # IMemoryRepository
│   │       ├── embedding_port.py      # IEmbeddingProvider
│   │       ├── embedding_cache_port.py# IEmbeddingCache
│   │       ├── tool_port.py           # ITool, IToolExecutor, ToolResult
│   │       ├── skill_port.py          # ISkillRepository
│   │       ├── history_port.py        # IHistoryStore
│   │       ├── knowledge_port.py      # IKnowledgeSource
│   │       ├── scheduler_port.py      # ISchedulerRepository
│   │       ├── scope_registry_port.py # IScopeRegistry (in-flight injection)
│   │       ├── background_delegation_port.py  # IBackgroundDelegationQueue
│   │       ├── broadcast_port.py      # IBroadcastChannel (multi-Pi TCP)
│   │       ├── vision_port.py         # IVisionPort (face detect + embed)
│   │       ├── face_registry_port.py  # IFaceRegistry (faces.db)
│   │       ├── scene_describer_port.py# ISceneDescriber (multimodal LLM description)
│   │       ├── transcription_port.py  # ITranscriptionProvider (voice → text)
│   │       ├── file_downloader_port.py# IFileDownloader (Telegram → bytes)
│   │       ├── channel_outbound_port.py # IChannelOutbound (envío saliente por canal)
│   │       ├── file_repo_port.py      # ITelegramFileRepo (local file cache)
│   │       ├── message_face_metadata_port.py # IMessageFaceMetadataRepo
│   │       ├── intermediate_sink_port.py      # IIntermediateSink
│   │       ├── outbound_sink_port.py  # IOutboundSink (response to channel)
│   │       └── daemon_client_port.py  # IDaemonClient (CLI ↔ remote daemon)
│   │
│   └── use_cases/
│       ├── run_agent.py               # RunAgentUseCase — one conversation turn
│       ├── run_agent_one_shot.py      # RunAgentOneShotUseCase — turn without history
│       ├── _tool_loop.py              # run_tool_loop() — LLM ↔ tools loop
│       ├── _result_parser.py          # LLM response parser
│       ├── consolidate_memory.py      # ConsolidateMemoryUseCase
│       ├── consolidate_all_agents.py  # ConsolidateAllAgentsUseCase
│       ├── schedule_task.py           # ScheduleTaskUseCase
│       ├── process_photo.py           # ProcessPhotoUseCase (facial + scene)
│       └── config/                    # Configuration CRUD (via TUI/REST admin)
│           ├── create_agent.py
│           ├── delete_agent.py
│           ├── update_agent_layer.py
│           ├── update_global_layer.py
│           ├── upsert_provider.py
│           ├── delete_provider.py
│           ├── get_effective_config.py
│           ├── list_agents.py
│           └── list_providers.py
│
├── adapters/
│   ├── inbound/
│   │   ├── turn_dispatch.py           # dispatch_inbound_turn() — in-flight routing compartido
│   │   ├── cli/
│   │   │   └── cli_runner.py          # Interactive terminal chat
│   │   ├── setup_tui/                 # Offline Textual TUI (inaki setup)
│   │   │   ├── app.py
│   │   │   ├── di.py                  # SetupContainer (schema Pydantic inyectado)
│   │   │   ├── screens/, widgets/, modals/, validators/
│   │   │   └── domain/, _schema.py, _cambios.py
│   │   ├── telegram/
│   │   │   ├── bot.py                 # Per-agent TelegramBot — wiring + turno privado
│   │   │   ├── ports.py               # TelegramBotPorts/Settings VOs (contrato con core)
│   │   │   ├── commands.py            # Mixin: comandos slash
│   │   │   ├── media.py               # Mixin: fotos, voz, video, documentos
│   │   │   ├── group_flow.py          # Mixin: routing de grupos + buffer-flush
│   │   │   ├── broadcast.py           # Mixin: emisión/trigger broadcast LAN
│   │   │   ├── message_mapper.py      # Update → Message, response → text
│   │   │   └── tools/                 # Telegram-specific tools
│   │   ├── rest/
│   │   │   └── admin/                 # Admin REST server — única superficie HTTP
│   │   │       ├── app.py             # create_admin_app()
│   │   │       ├── ports.py           # AdminAgentContainer/AppContainer Protocols
│   │   │       ├── schemas.py
│   │   │       └── routers/           # admin, chat, tools, deps
│   │
│   ├── broadcast/
│   │   └── tcp.py                     # BroadcastTCPServer / BroadcastTCPClient
│   │
│   └── outbound/
│       ├── providers/                 # LLM — dynamic discovery via PROVIDER_NAME
│       │   ├── base.py
│       │   ├── openrouter.py
│       │   ├── openai.py
│       │   ├── openai_responses.py
│       │   ├── groq.py
│       │   ├── ollama.py
│       │   └── deepseek.py
│       ├── embedding/                 # Embedding — dynamic discovery
│       │   ├── base.py
│       │   ├── e5_onnx.py
│       │   ├── openai.py
│       │   └── sqlite_embedding_cache.py
│       ├── transcription/             # Voice → text — dynamic discovery
│       │   ├── base.py
│       │   └── groq.py
│       ├── memory/
│       │   └── sqlite_memory_repo.py
│       ├── history/
│       │   ├── sqlite_history_store.py
│       │   └── sqlite_message_face_metadata_repo.py
│       ├── skills/
│       │   └── yaml_skill_repo.py
│       ├── knowledge/
│       │   ├── document_knowledge_source.py
│       │   ├── sqlite_knowledge_source.py
│       │   ├── sqlite_memory_knowledge_source.py
│       │   └── _chunker.py
│       ├── tools/
│       │   ├── tool_registry.py
│       │   ├── delegate_tool.py       # Agent-to-agent delegation
│       │   ├── memory_tools.py        # search/delete/update_memory
│       │   ├── scheduler_tool.py
│       │   ├── knowledge_search_tool.py
│       │   ├── face_tools.py          # enroll_face, skip_face, list_faces
│       │   ├── read_file_tool.py
│       │   ├── write_file_tool.py
│       │   ├── edit_file_tool.py
│       │   ├── patch_file_tool.py
│       │   ├── web_search_tool.py
│       │   └── path_resolution.py
│       ├── scheduler/
│       │   ├── sqlite_scheduler_repo.py
│       │   ├── dispatch_adapters.py   # LLMDispatcherAdapter, ChannelRouter, etc.
│       │   └── builtin_tasks.py       # consolidate_memory, face_dedup
│       ├── delegation/
│       │   └── background_queue_adapter.py  # Async queue (semaphore = 3)
│       ├── faces/
│       │   └── sqlite_face_registry.py
│       ├── vision/
│       │   └── insightface_adapter.py # IVisionPort (lazy-load on first photo)
│       ├── scene/
│       │   ├── anthropic_describer.py
│       │   ├── openai_describer.py
│       │   └── groq_describer.py
│       ├── sinks/
│       │   ├── sink_factory.py
│       │   ├── telegram_sink.py
│       │   ├── file_sink.py
│       │   └── null_sink.py
│       ├── intermediate_sinks/
│       │   ├── buffering.py
│       │   ├── channel_router.py
│       │   └── telegram_live.py
│       ├── file_transport/
│       │   ├── telegram_file_downloader.py
│       │   └── telegram_file_sender.py
│       ├── file_repo/
│       │   └── sqlite_telegram_file_repo.py
│       ├── imaging/
│       │   └── pillow_annotator.py
│       ├── config_repository/
│       │   ├── yaml_repository.py     # IConfigRepository over YAML in ~/.inaki/
│       │   └── paths.py
│       ├── scope_registry_adapter.py  # InMemoryScopeRegistryAdapter
│       └── daemon_client.py           # DaemonClient (HTTP → admin REST)
│
├── infrastructure/
│   ├── container.py                   # AgentContainer + AppContainer (single wiring)
│   ├── config.py                      # Pydantic v2 models + 4-layer loader
│   ├── logging_setup.py
│   ├── daemon_reloader.py             # DaemonReloader (hot-reload)
│   └── factories/
│       ├── llm_factory.py             # Dynamic discovery providers/
│       ├── embedding_factory.py       # Dynamic discovery embedding/
│       └── transcription_factory.py   # Dynamic discovery transcription/
│
├── ext/                               # User extensions (auto-discovery)
│   └── {extension}/
│       ├── manifest.py
│       └── *.py / *.yaml
│
├── config/
│   └── global.example.yaml            # Canonical reference for all parameters
│
├── docs/                              # Technical documentation
├── systemd/                           # inaki.service + install.sh
├── tests/
│   ├── conftest.py                    # Shared fixtures
│   ├── unit/
│   └── integration/
│
├── inaki/                             # Composition root (importa infrastructure)
│   ├── cli.py                         # Entry point (typer)
│   ├── daemon_runner.py               # run_daemon — arranca todos los canales
│   ├── scheduler_cli.py              # inaki scheduler ...
│   ├── knowledge_cli.py              # inaki knowledge ...
│   ├── setup_cli.py                  # inaki setup (inyecta schema al setup_tui)
│   └── __version__
├── main.py
└── pyproject.toml
```

**User data — always in `~/.inaki/`:**

```
~/.inaki/
├── config/
│   ├── global.yaml
│   ├── global.secrets.yaml            # gitignored — never commit
│   ├── tool_config.yaml               # daemon-owned; tool credentials (enc: inside); not in 4-layer merge
│   └── agents/
│       ├── {id}.yaml
│       └── {id}.secrets.yaml          # gitignored
├── data/
│   ├── inaki.db                       # Memories (sqlite-vec)
│   ├── history.db                     # Conversation history
│   ├── faces.db                       # Face registry (created on first use)
│   └── embedding_cache.db             # Embedding cache
├── models/
│   └── e5-small/                      # ONNX model + tokenizer
└── mem/
    └── digest_{channel}_{chat_id}.md  # Memory digest per scope
```

---

## 4. Domain Entities

```python
# core/domain/entities/message.py
class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"

class Message(BaseModel):
    role: Role
    content: str
    tool_calls: list[dict] | None = None  # for assistant role with calls
    tool_call_id: str | None = None        # for tool role (result)
```

```python
# core/domain/entities/memory.py
class MemoryEntry(BaseModel):
    id: str                          # UUID
    content: str
    embedding: list[float]           # dimension 384 (e5-small)
    relevance: float                 # 0.0–1.0, estimated by LLM extractor
    tags: list[str]
    created_at: datetime
    agent_id: str | None = None
    channel: str | None = None       # scope (channel, chat_id) of origin
    chat_id: str | None = None
    deleted: int = 0                 # soft-delete: 0 = active, 1 = deleted
    reconciled: int = 0              # 0 = pending reconciliation, 1 = already processed
```

```python
# core/domain/entities/task.py
class TaskType(str, Enum):
    RECURRENT = "recurrent"   # cron
    ONESHOT = "oneshot"       # exact datetime

class TaskStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"

class ScheduledTask(BaseModel):
    id: str
    agent_id: str
    description: str
    task_kind: TaskType
    schedule: str                    # cron expr or ISO datetime
    system_prompt_override: str | None = None
    status: TaskStatus = TaskStatus.ACTIVE
```

```python
# core/domain/entities/face.py
class FaceDetection(BaseModel):
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    embedding: list[float]           # 512 floats (InsightFace)
    confidence: float

class Person(BaseModel):
    id: str
    name: str
    categoria: str | None = None     # None = normal, "ignorada" = permanent skip
```

---

## 5. Key Ports

### `ILLMProvider`

```python
class ILLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse: ...
```

`LLMResponse` encapsulates the text and `tool_calls` returned by the model.

### `IHistoryStore`

```python
class IHistoryStore(ABC):
    async def append(self, agent_id: str, message: Message, channel: str, chat_id: str) -> None: ...
    async def load(self, agent_id: str, channel: str, chat_id: str, limit: int) -> list[Message]: ...
    async def clear(self, agent_id: str, channel: str, chat_id: str) -> None: ...
    async def record_user_message(self, agent_id: str, message: Message, channel: str, chat_id: str) -> None: ...
    async def drain_pending(self, agent_id: str, channel: str, chat_id: str) -> list[Message]: ...
```

History is stored in **SQLite** (`history.db`). It is scoped by `(agent_id, channel, chat_id)`. `record_user_message` + `drain_pending` support in-flight message injection.

### `IScopeRegistry`

```python
class IScopeRegistry(ABC):
    async def try_mark_busy(self, scope: Scope) -> bool: ...
    async def mark_idle(self, scope: Scope) -> None: ...
```

`Scope = tuple[str, str, str]` — `(agent_id, channel, chat_id)`. Implemented with `asyncio.Lock` in `InMemoryScopeRegistryAdapter`. A single instance is shared across all agents (scopes are disjoint by `agent_id`).

### `IMemoryRepository`

```python
class IMemoryRepository(ABC):
    async def store(self, entry: MemoryEntry) -> None: ...
    async def search(self, query_embedding: list[float], top_k: int, agent_id: str, channel: str, chat_id: str) -> list[MemoryEntry]: ...
    async def get_recent(self, limit: int, agent_id: str, channel: str, chat_id: str) -> list[MemoryEntry]: ...
    async def delete(self, memory_id: str) -> bool: ...        # soft-delete
    async def update(self, memory_id: str, content: str) -> bool: ...
```

### `IVisionPort`

```python
class IVisionPort(ABC):
    async def detect_and_embed(self, image_bytes: bytes) -> list[FaceDetection]: ...
```

Implemented by `InsightFaceAdapter`. The model is **lazily** loaded on the first call (`_get_app()` with lazy singleton). It uses ~400 MB of RAM.

### `ITool` / `IToolExecutor`

```python
class ToolResult(BaseModel):
    tool_name: str
    output: str
    success: bool
    error: str | None = None

class ITool(ABC):
    name: str
    description: str
    parameters_schema: dict   # JSON Schema (OpenAI function calling format)
    async def execute(self, **kwargs) -> ToolResult: ...
```

---

## 6. Use Cases

### `RunAgentUseCase`

Orchestrates a full conversation turn. The phases live as free functions in
`_turn_pipeline.py` (same contract as `_tool_loop.py`: explicit dependencies,
no `self`) and `_execute_turn` chains them:

1. Load history scoped by `(agent_id, channel, chat_id)`
2. `run_semantic_routing()` — if active: generate input embedding, filter relevant tools/skills via cosine similarity, apply sticky TTL (with short-input bypass)
3. `prefetch_knowledge()` — retrieve knowledge chunks reusing the query embedding (shared with `inspect()`)
4. Build `AgentContext` and dynamic system prompt (base + memory digest + skills)
5. `assemble_turn_messages()` — direct `user_input` vs history-derived coalesced batch
6. Call the LLM via `run_tool_loop()` — see S7
7. Persist `user` / `assistant` messages in history (never `tool` or `tool_result`)
8. Return `ChatTurnResult`

### `_tool_loop.run_tool_loop()`

LLM-tools loop until `tool_call_max_iterations` (default 5) is exhausted or the LLM stops calling tools:

- **Circuit breaker:** if the same tool fails `circuit_breaker_threshold` consecutive times, the loop is cut.
- **In-flight injection:** between iterations (checkpoints A: before `llm.complete`, B: after the tool_calls batch), pending user messages are drained via `history_store.drain_pending()`. If messages are found, the iteration counter resets to 0.
- Backward-compatible: `history_store=None` disables injection (legacy mode).

### `ConsolidateMemoryUseCase`

1. Load undigested history from the scope
2. LLM extracts memories as JSON
3. Generate embedding for each memory (`embed_passage`)
4. Persist in `IMemoryRepository` (DELETE + INSERT to avoid UNIQUE bug in `vec0`)
5. If everything succeeds: archive and clear history. If it fails: history remains intact (transactional)

### `ReconcileMemoryUseCase`

Revisits existing memories to resolve contradictions and redundancies. Runs as a nightly scheduled task (`reconcile_memory_{agent_id}`, cron from `memories.reconciliation.schedule`).

1. `load_unreconciled(agent_id)` — fetches seeds: active memories with `reconciled=0`
2. For each seed: `search_with_scores()` retrieves the `top_k` most similar neighbors by cosine similarity within the same `(channel, chat_id)` scope; neighbors below `similarity_threshold` are discarded
3. An LLM (the agent's own or a dedicated `memory_reconciler` sub-agent) receives the cluster and decides one action per group: `merge` (creates a new entry + soft-deletes the originals), `supersede` (soft-deletes outdated entries), `downweight` (reduces relevance), or `keep` (no-op)
4. Actions are applied; processed seeds are marked `reconciled=1` via `mark_reconciled(ids)` — **never globally**
5. Entries created by `merge` are born with `reconciled=True` (anti-loop: they are not re-processed until a new neighbor surfaces)
6. Best-effort per cluster: a cluster that fails does not abort the rest (unlike `ConsolidateMemoryUseCase`, which is transactional)

**Canonical case:** "estoy enfermo, tomo tratamiento X" (old) + "ya me recuperé" (new) → `merge` into a single updated memory that preserves the timeline, soft-deleting the originals.

### `ProcessPhotoUseCase`

1. Download the image from Telegram via `IFileDownloader`
2. Call `IVisionPort.detect_and_embed()` → list of `FaceDetection`
3. For each face: search in `IFaceRegistry` by cosine similarity
4. Based on the result (MATCHED / AMBIGUOUS / UNKNOWN / IGNORED): decide whether to enroll, ignore, or request confirmation
5. Call `ISceneDescriber.describe()` → text description of the scene
6. Persist metadata in `IMessageFaceMetadataRepo` (side-table in `history.db`, `ON DELETE CASCADE`)
7. Return combined response (recognitions + description)

### Config Use Cases (`core/use_cases/config/`)

YAML configuration CRUD through `IConfigRepository`. Used by the TUI and admin REST. Operates on `~/.inaki/config/` without touching the repository.

---

## 7. Configuration System

### 4-layer merge (field by field)

```
~/.inaki/config/global.yaml
        ↓ field-by-field merge
~/.inaki/config/global.secrets.yaml
        ↓ field-by-field merge
~/.inaki/config/agents/{id}.yaml
        ↓ field-by-field merge
~/.inaki/config/agents/{id}.secrets.yaml
        ↓
Resolved AgentConfig
```

- Each layer only overrides the fields it defines. Absent fields are inherited.
- `*.secrets.yaml` files are in `.gitignore` and **must never be committed**.
- The canonical, commented reference for all parameters is `config/global.example.yaml`.

### Provider registry

```yaml
providers:
  openrouter: { api_key: "sk-or-..." }
  groq:       { api_key: "gsk_..." }
  openai:     { api_key: "sk-..." }
```

`llm.provider`, `embedding.provider`, and `transcription.provider` reference a key from this dict. Credentials live **only** in the registry, never in feature blocks.

### Relevant config models

```python
class GlobalConfig(BaseModel):
    app: AppConfig
    providers: dict[str, ProviderEntry]
    llm: LLMConfig
    embedding: EmbeddingConfig
    memories: MemoriesConfig
    chat_history: ChatHistoryConfig
    tools: ToolsConfig
    skills: SkillsConfig
    workspace: WorkspaceConfig
    admin: AdminConfig
    transcription: TranscriptionConfig | None
    knowledge: KnowledgeConfig | None
    photos: PhotosConfig | None
    channels: GlobalChannelsConfig

class AgentConfig(GlobalConfig):
    id: str
    name: str
    description: str
    system_prompt: str
    channels: dict[str, dict]   # telegram, cli, broadcast — per agent
```

### Settings VOs — config never crosses into `core/`

Use cases do **not** receive `AgentConfig`. Each one declares the parameters it
consumes as a frozen VO in `core/domain/value_objects/agent_settings.py`
(`RunAgentSettings`, `OneShotSettings`, `MemorySettings`, `PhotosSettings`).
The config→VO mapping lives in the public builders of
`infrastructure/container.py` (`build_run_agent_settings`, etc.) — the only
point where both worlds touch. Enforced by `tests/unit/test_architecture.py`.

---

## 8. Multi-Agent System

### Startup

`AppContainer.__init__()`:
1. Builds one `AgentContainer` per agent (first pass)
2. Registers the `delegate` tool in each container with references to the others (second pass — necessary because containers must exist before cross-references)
3. Starts `SchedulerService` with all agents

### History and memory scope

- **History:** scoped by `(agent_id, channel, chat_id)`. Conversations in Telegram groups, private chats, and CLI are completely isolated.
- **Memory:** optionally scoped by `(agent_id, channel, chat_id)`. `channel=NULL, chat_id=NULL` = global pre-migration memories.
- **Agent state** (sticky tools/skills): scoped by `(agent_id, channel, chat_id)`.

### Delegation

The `delegate` tool allows one agent to invoke another. Two modes:

- **`wait=true`** — synchronous (legacy): blocks until a `DelegationResult` is received.
- **`wait=false`** — asynchronous (default): enqueues in `BackgroundDelegationQueueAdapter` (semaphore = 3), returns `bg-N` instantly. When finished, the result is injected as `Role.USER` with prefix `[bg-N]` into the origin scope via `LLMDispatcherAdapter`.

`LLMDispatcherAdapter` is built **once** in `AppContainer` and shared between the queue adapter and `SchedulerService`. This serializes turns on the same `(agent_id, channel, chat_id)` via lock-per-scope.

**Per-delegation inheritance (ephemeral child).** A delegation does NOT run the sub-agent's pre-built `run_agent_one_shot` (which is resolved against `global`). Instead it builds an **ephemeral one-shot instance resolved against the CALLER** via `AgentContainer.build_ephemeral_child(definition_raw)`: `resolve_inherit(_deep_merge(SUBAGENT_DEFAULTS, definition_raw), parent_raw)`, where `parent_raw` is the caller's *effective* config. The `inherit` primitive — a per-block merge directive resolved in raw dicts **before** pydantic and then stripped (never a model field) — makes the child inherit from the parent: the `llm` block by default (via `SUBAGENT_DEFAULTS`), the rest opt-in.

- **Tools and resources are ALWAYS the caller's** (`caller._tools`: the parent's workspace, memory and knowledge). The sub narrows the *visible* subset with its own `tools.allowed` field (a filter in `RunAgentOneShotUseCase.execute`, REQ-OS-5, alongside the `delegate` exclusion REQ-DG-9). The caller never overrides the sub's tools — the sub's definition is the sole authority on its tool access.
- **LLM instance reuse**: if the child's effective `llm` matches the caller's, the caller's instance is reused; if the sub overrides it, a new one is built via `LLMProviderFactory` with the `providers` (credentials) inherited from the caller. No embedder is wired (the one-shot exposes the full toolkit without RAG — REQ-OS-4).
- The **same sub definition** delegated by P and by Q inherits **different LLMs** (per-caller, not per-definition). Both the sync (`wire_delegation` → `build_child`) and async (`BackgroundDelegationQueueAdapter`, `one_shot_resolver(caller_id, target_id)`) paths resolve the ephemeral child against the caller.

Scope: this inheritance applies ONLY to the `delegate` flow. The memory rail (extractor / reconciler sub-agents) inherits the parent LLM on its own via `merged_llm_config`. The shared pool of sub-agent *definitions* is unchanged — what changes is that each delegation builds a fresh instance resolved against whoever delegates.

### In-flight message injection

When a new message arrives on a scope that already has an `execute()` in progress:

```
if try_mark_busy(scope):
    try: execute() finally: mark_idle(scope)
else:
    record_user_message(message)
    return "incorporating into the task in progress..."
```

The tool loop drains those messages between iterations and incorporates them into the LLM context. When drained messages are received, the iteration counter resets. The circuit breaker does **not** reset (tool failures keep accumulating).

The routing is centralized in `dispatch_inbound_turn()` (`adapters/inbound/turn_dispatch.py`) with a single shared ACK constant — Telegram private chats and the admin chat endpoint go through it. The Telegram photo handler is the deliberate exception: it acquires the slot **before** the heavy photo processing and decides the path at the end, so it only shares the ACK constant.

**Telegram groups excluded**: the group pipeline uses natural buffer+delay coalescing; in-flight injection does not apply there.

---

## 9. Channels

### CLI

```bash
inaki                            # default agent, interactive
inaki chat --agent dev           # specific agent
inaki --remote http://host:6497  # connect to remote daemon
```

### Telegram

One bot per agent (one token per agent). Config in `agents/{id}.yaml`:

```yaml
channels:
  telegram:
    allowed_user_ids: ["123456789"]
    allowed_chat_ids: []           # authorized groups (negative IDs)
    reactions: true
    voice_enabled: true            # Whisper transcription
```

**Groups:** pipeline with buffer + random delay + coalescing of consecutive messages from the same author. Group messages are coalesced before sending to the LLM to avoid broken `user/assistant` alternation.

**Multi-Pi broadcast:** see S11.

### REST API

A single **admin server** (port 6497, `127.0.0.1` by default) is the only HTTP surface of the daemon. Routing is by `agent_id` in the request; auth via `X-Admin-Key` (timing-safe comparison, fail-closed: no key configured → 403). There is no per-agent REST server.

Endpoints: `/health`, `/inspect`, `/consolidate` (per-agent with `agent_id` in body, or all), `/scheduler/reload`, `/admin/reload`, `/admin/agents`, `/admin/agent/info`, `/admin/chat/turn` (accepts optional `channel`/`chat_id` to operate on a real history scope), `/admin/chat/task`, `/admin/chat/history`, `/admin/tool/list`, `/admin/tool/invoke`, `/admin/send`. See `docs/configuracion.md` for bodies and examples.

---

## 10. Semantic Routing

Tools and skills are selected by cosine similarity (input embedding vs. description/name embedding):

- **Without routing** (< `semantic_routing_min_tools`/`min_skills` tools/skills): all are passed to the LLM.
- **With routing**: scores are computed, the top-K exceeding `min_score` are passed.
- **Sticky:** if the LLM used a tool/skill in the previous turn, it stays in context for `sticky_ttl` turns even if routing would not include it. Implemented in `StickySelector`.

The sticky state is persisted in `agent_state` in `history.db`, scoped by `(agent_id, channel, chat_id)`.

---

## 11. Multi-Pi Broadcast

Allows multiple Inaki instances on the same LAN to share a Telegram group. The Bot API does not deliver messages from other bots, so a TCP side channel is used.

**Topology:** star — one server (`broadcast.port`), N clients (`broadcast.remote.host`).  
**Wire format:** JSON line-delimited, signed HMAC-SHA256 with a 60 s freshness window.  
**Behavior modes:** `listen` (receive only) | `mention` (responds when mentioned) | `autonomous` (responds if it considers it can contribute, may emit `[SKIP]`).  
**Rate limiter:** `FixedWindowRateLimiter` to prevent infinite loops in `autonomous` mode.

---

## 12. Optional Features

### Face recognition (Telegram photos)

Enabled with `photos.enabled: true`. Pipeline:

1. `IVisionPort.detect_and_embed()` → `list[FaceDetection]` (InsightFace, lazy-load)
2. `IFaceRegistry` searches by embedding in `faces.db` (sqlite-vec FLOAT[512])
3. `ISceneDescriber.describe()` → multimodal LLM description

Database `faces.db` is independent from `history.db` and `inaki.db`. The `persons` table uses `categoria VARCHAR`: `NULL` = normal person, `"ignorada"` = permanently ignored.

Warning: Changing `faces.model` invalidates `faces.db` — delete and re-enroll.  
Warning: `schema_meta.embedding_dim` is validated at startup. Mismatch → `EmbeddingDimensionMismatchError`.

### Voice transcription

Enabled with `channels.telegram.voice_enabled: true` (default). Uses `ITranscriptionProvider` (Groq Whisper). The provider is dynamically discovered just like LLM and embedding. Documents with `audio/*` mime route here too. The turn's user message is an attachment block (`@audio ... at <local_path>` + `@transcription: <text>`); early exits (disabled, too large, failed transcription) still persist the `@audio` block — see the `attachment-grammar` migration note in `CLAUDE.md`.

### Knowledge Sources (RAG over documents)

Configured in `knowledge.sources`. Three types: `document` (Markdown, PDF on disk), `sqlite` (SQLite table), `memory` (fusion with agent memories). The `KnowledgeOrchestrator` aggregates results from all sources with a token budget.

---

## 13. Scheduler

`SchedulerService` runs an async loop. Two task types:

- **RECURRENT** — cron expression (`croniter`). Fires when `next_run_time <= now`.
- **ONESHOT** — exact ISO datetime. Fires once and transitions to `DONE`.

Tasks are persisted in `scheduler.db` (or in `history.db`, depending on config). The dispatcher (`SchedulerDispatchPorts`) routes execution based on task type: to `LLMDispatcherAdapter`, to `ConsolidationDispatchAdapter`, or to `HttpCallerAdapter`.

Built-in tasks registered automatically: `consolidate_memory` (nightly, cron from `memories.consolidation.schedule`), `reconcile_memory_{agent_id}` (one per agent with `memories.reconciliation.enabled: true`, cron from `memories.reconciliation.schedule`), and `face_dedup` (if `photos.dedup.enabled`).

---

## 14. Extensions (`ext/`)

Auto-discovery mechanism: any folder in `ext/` with a `manifest.py` declaring the package is loaded automatically. Tools implementing `ITool` and YAML skills following the convention are registered without touching anything in `core/` or `infrastructure/`.

Included extensions: `exchange_calendar`, `nominatim`, `notes_todo_list`, `replicate_music`, `shell_exec`.

Conventions:

| Element | Convention |
|---|---|
| Tool file | `{name}_tool.py` |
| Tool class | `{Name}Tool` |
| `ITool.name` | `snake_case` |
| Skill | `{name}.yaml` with fields `name`, `description`, `content` |

---

## 15. Provider Factories (Dynamic Discovery)

The factories scan their directories, import modules, read `PROVIDER_NAME`, and build an in-memory registry. Adding a provider = creating the file with the correct `PROVIDER_NAME`. Nothing else to touch.

Applies to: `adapters/outbound/providers/` (LLM), `adapters/outbound/embedding/` (embedding), `adapters/outbound/transcription/` (voice).

```python
# Mandatory convention
PROVIDER_NAME = "mi_proveedor"

class MiProvider(BaseLLMProvider):
    ...
```

---

## 16. Testing

- `pytest-asyncio` in `auto` mode — no `@pytest.mark.asyncio` needed.
- Shared fixtures in `tests/conftest.py`: `agent_config` (`:memory:` DB), `mock_llm`, `mock_memory`, `mock_embedder`, `mock_skills`, `mock_history`, `mock_tools`.
- Unit tests: mock all ports. No SQLite, ONNX, or network.
- Integration tests: real SQLite in memory or temporary file.

```bash
pytest                          # all
pytest tests/unit/              # unit only
pytest tests/integration/       # integration only
pytest -k test_name             # specific test
```

---

## 17. Error Handling

```python
# core/domain/errors.py
class InakiError(Exception): ...
class AgentNotFoundError(InakiError): ...
class LLMError(InakiError): ...
class ConsolidationError(InakiError): ...
class EmbeddingError(InakiError): ...
class EmbeddingDimensionMismatchError(InakiError): ...
class ToolLoopMaxIterationsError(InakiError): ...
class ConfigError(InakiError): ...
```

Adapters log at their layer and propagate typed exceptions upward. The core never logs directly — it uses exceptions to communicate errors.

---

## 18. Development Rules

When adding any new functionality, this is the mandatory order:

1. **Entity/Value Object** in `core/domain/` if a new concept is introduced
2. **Port** in `core/ports/` if a new external dependency is needed
3. **Use Case** in `core/use_cases/` with the orchestration
4. **Unit test** in `tests/unit/` with port mocks — before the adapter
5. **Adapter** in `adapters/outbound/` or `adapters/inbound/`
6. **Wiring** in `infrastructure/container.py`
7. **Config** in `config/global.example.yaml` if new parameters are required

**Never skip steps. Never mix layers.**

---

*Version: 2.x — Updated to reflect the complete system post-`drop-per-agent-rest` (settings VOs, ContextVar per-turn, dispatch_inbound_turn, admin-only HTTP surface).*
