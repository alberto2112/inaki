# Inaki — Especificación Técnica

> Documento de referencia para el desarrollo del agente Inaki.  
> Refleja el estado real del sistema en v2.x.

---

## 1. Visión General

Inaki es un asistente personal agéntico impulsado por IA, diseñado para ejecutarse como servicio systemd en una **Raspberry Pi 5 (4 GB RAM, ARM64)**. El proyecto sigue una **arquitectura hexagonal (Ports & Adapters)** estricta para garantizar modularidad, testabilidad y extensibilidad.

### Principios de diseño

- **El core no conoce el mundo exterior.** Ningún archivo de `core/` importa de `adapters/` ni de librerías de infraestructura. Solo stdlib + tipos del propio `core/`.
- **Dirección de dependencias inviolable:** `adapters/` → `core/`. Nunca al revés.
- **Un único punto de wiring:** `infrastructure/container.py` es el único lugar donde se instancian adaptadores concretos.
- **Configuración en `~/.inaki/`:** todos los datos del usuario (configs, secrets, DBs, modelos) viven fuera del repo.
- **Diseñado para la Pi 5:** footprint de RAM, ARM64 y coste de tokens son restricciones de primera clase.

---

## 2. Stack Tecnológico

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.11+ |
| Hardware destino | Raspberry Pi 5, 4 GB RAM, ARM64 |
| Despliegue | systemd service |
| LLM providers | OpenRouter, OpenAI, Groq, Ollama, DeepSeek (descubrimiento dinámico) |
| Embeddings | `multilingual-e5-small` (ONNX) · OpenAI (alternativa) |
| Vector store | `sqlite-vec` + SQLite3 |
| Historial | SQLite3 (`aiosqlite`) |
| Config | YAML · fusión de 4 capas · `pydantic` v2 |
| Tests | `pytest` + `pytest-asyncio` (modo `auto`) |
| CLI | `typer` + `rich` |
| TUI configuración | `textual` + `ruamel.yaml` |
| Inbound Telegram | `python-telegram-bot` v21+ (async) |
| Inbound REST | `FastAPI` + `uvicorn` |
| HTTP client | `httpx` (async) |
| Face recognition | `InsightFace` (lazy-loaded, ~400 MB RAM) |
| Scheduler | `croniter` |
| Transcripción voz | Whisper via Groq API |

---

## 3. Estructura de Directorios

```
inaki/                                  ← raíz del repositorio
│
├── core/                               ← Hexágono: cero dependencias externas
│   ├── domain/
│   │   ├── entities/
│   │   │   ├── message.py              # Message, Role
│   │   │   ├── memory.py              # MemoryEntry
│   │   │   ├── skill.py               # Skill, SkillResult
│   │   │   ├── task.py                # ScheduledTask, TaskStatus, TaskType
│   │   │   ├── task_log.py            # TaskLog (historial de ejecuciones)
│   │   │   ├── face.py                # FaceDetection, Person, KnownFace
│   │   │   └── background_task.py     # BackgroundTaskView (delegación async)
│   │   ├── value_objects/
│   │   │   ├── agent_context.py       # AgentContext → build_system_prompt()
│   │   │   ├── agent_info.py          # AgentInfoDTO
│   │   │   ├── channel_context.py     # ChannelContext (channel, chat_id, extras)
│   │   │   ├── chat_turn_result.py    # ChatTurnResult
│   │   │   ├── conversation_state.py  # ConversationState
│   │   │   ├── delegation_result.py   # DelegationResult
│   │   │   ├── dispatch_result.py     # DispatchResult
│   │   │   ├── embedding.py           # Embedding(vector, model)
│   │   │   ├── knowledge_chunk.py     # KnowledgeChunk (RAG)
│   │   │   ├── llm_response.py        # LLMResponse (text + tool_calls)
│   │   │   └── telegram_file.py       # TelegramFile (id, mime_type, bytes)
│   │   ├── services/
│   │   │   ├── scheduler_service.py   # SchedulerService (cron loop)
│   │   │   ├── knowledge_orchestrator.py  # RAG multi-fuente
│   │   │   ├── sticky_selector.py     # Sticky semantic routing (TTL)
│   │   │   ├── rate_limiter.py        # FixedWindowRateLimiter
│   │   │   ├── broadcast_buffer.py    # Buffer de mensajes de grupo
│   │   │   ├── prepend_timestamps.py  # Inyecta timestamps en historial
│   │   │   └── similarity.py          # Cosine similarity utils
│   │   ├── utils/
│   │   │   └── time_parser.py         # Parser de expresiones de tiempo (ONESHOT)
│   │   └── errors.py                  # InakiError y subclases
│   │
│   ├── ports/
│   │   ├── config_repository.py       # IConfigRepository (lectura/escritura YAML)
│   │   ├── inbound/
│   │   │   ├── agent_port.py          # IAgentUseCase
│   │   │   └── scheduler_port.py      # ISchedulerUseCase
│   │   └── outbound/
│   │       ├── llm_port.py            # ILLMProvider
│   │       ├── llm_dispatcher_port.py # ILLMDispatcher (turno completo scoped)
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
│   │       ├── scene_describer_port.py# ISceneDescriber (descripción LLM multimodal)
│   │       ├── transcription_port.py  # ITranscriptionProvider (voz → texto)
│   │       ├── file_downloader_port.py# IFileDownloader (Telegram → bytes)
│   │       ├── file_sender_port.py    # IFileSender (bytes → Telegram)
│   │       ├── file_repo_port.py      # ITelegramFileRepo (caché local de archivos)
│   │       ├── message_face_metadata_port.py # IMessageFaceMetadataRepo
│   │       ├── intermediate_sink_port.py      # IIntermediateSink
│   │       ├── outbound_sink_port.py  # IOutboundSink (respuesta hacia canal)
│   │       └── daemon_client_port.py  # IDaemonClient (CLI ↔ daemon remoto)
│   │
│   ├── services/
│   │   └── crypto_service.py          # CryptoService (Fernet, secrets)
│   │
│   └── use_cases/
│       ├── run_agent.py               # RunAgentUseCase — un turno de conversación
│       ├── run_agent_one_shot.py      # RunAgentOneShotUseCase — turno sin historial
│       ├── _tool_loop.py              # run_tool_loop() — loop LLM ↔ tools
│       ├── _result_parser.py          # Parser de respuestas LLM
│       ├── consolidate_memory.py      # ConsolidateMemoryUseCase
│       ├── consolidate_all_agents.py  # ConsolidateAllAgentsUseCase
│       ├── schedule_task.py           # ScheduleTaskUseCase
│       ├── process_photo.py           # ProcessPhotoUseCase (facial + escena)
│       └── config/                    # CRUD de configuración (via TUI/REST admin)
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
│   │   ├── cli/
│   │   │   ├── cli_runner.py          # Chat interactivo por terminal
│   │   │   ├── scheduler_cli.py       # inaki scheduler ...
│   │   │   ├── knowledge_cli.py       # inaki knowledge ...
│   │   │   ├── setup_cli.py           # inaki setup ...
│   │   │   └── setup_wizard.py        # Wizard Fernet legacy
│   │   ├── setup_tui/                 # TUI Textual offline (inaki setup)
│   │   │   ├── app.py
│   │   │   ├── screens/, widgets/, modals/, validators/
│   │   │   └── domain/, _schema.py, _cambios.py
│   │   ├── telegram/
│   │   │   ├── bot.py                 # TelegramBot per-agent (PTB 21+)
│   │   │   ├── message_mapper.py      # Update → Message, respuesta → texto
│   │   │   └── tools/                 # Tools Telegram-específicas
│   │   ├── rest/
│   │   │   ├── app.py                 # create_agent_app() — por agente
│   │   │   ├── schemas.py
│   │   │   ├── routers/               # GET /info, POST /chat, etc.
│   │   │   └── admin/                 # Admin REST server (daemon)
│   │   └── daemon/
│   │       └── runner.py              # DaemonRunner — levanta todos los agentes
│   │
│   ├── broadcast/
│   │   └── tcp.py                     # BroadcastTCPServer / BroadcastTCPClient
│   │
│   └── outbound/
│       ├── providers/                 # LLM — descubrimiento dinámico por PROVIDER_NAME
│       │   ├── base.py
│       │   ├── openrouter.py
│       │   ├── openai.py
│       │   ├── openai_responses.py
│       │   ├── groq.py
│       │   ├── ollama.py
│       │   └── deepseek.py
│       ├── embedding/                 # Embedding — descubrimiento dinámico
│       │   ├── base.py
│       │   ├── e5_onnx.py
│       │   ├── openai.py
│       │   └── sqlite_embedding_cache.py
│       ├── transcription/             # Voz → texto — descubrimiento dinámico
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
│       │   ├── delegate_tool.py       # Delegación agente-a-agente
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
│       │   └── background_queue_adapter.py  # Cola async (semáforo 3)
│       ├── faces/
│       │   └── sqlite_face_registry.py
│       ├── vision/
│       │   └── insightface_adapter.py # IVisionPort (lazy-load en primera foto)
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
│       │   ├── yaml_repository.py     # IConfigRepository sobre YAML en ~/.inaki/
│       │   └── paths.py
│       ├── scope_registry_adapter.py  # InMemoryScopeRegistryAdapter
│       └── daemon_client.py           # DaemonClient (HTTP → admin REST)
│
├── infrastructure/
│   ├── container.py                   # AgentContainer + AppContainer (único wiring)
│   ├── config.py                      # Modelos pydantic v2 + loader 4 capas
│   ├── logging_setup.py
│   ├── daemon_reloader.py             # DaemonReloader (hot-reload)
│   └── factories/
│       ├── llm_factory.py             # Descubrimiento dinámico providers/
│       ├── embedding_factory.py       # Descubrimiento dinámico embedding/
│       └── transcription_factory.py   # Descubrimiento dinámico transcription/
│
├── ext/                               # Extensiones del usuario (auto-descubrimiento)
│   └── {extension}/
│       ├── manifest.py
│       └── *.py / *.yaml
│
├── config/
│   └── global.example.yaml            # Referencia canónica de todos los parámetros
│
├── docs/                              # Documentación técnica
├── systemd/                           # inaki.service + install.sh
├── tests/
│   ├── conftest.py                    # Fixtures compartidos
│   ├── unit/
│   └── integration/
│
├── inaki/
│   ├── cli.py                         # Entry point (typer)
│   └── __version__
├── main.py
└── pyproject.toml
```

**Datos del usuario — siempre en `~/.inaki/`:**

```
~/.inaki/
├── config/
│   ├── global.yaml
│   ├── global.secrets.yaml            # gitignoreado — nunca commitear
│   └── agents/
│       ├── {id}.yaml
│       └── {id}.secrets.yaml          # gitignoreado
├── data/
│   ├── inaki.db                       # Memorias (sqlite-vec)
│   ├── history.db                     # Historial de conversación
│   ├── faces.db                       # Registro facial (creado en primer uso)
│   └── embedding_cache.db             # Caché de embeddings
├── models/
│   └── e5-small/                      # ONNX model + tokenizer
└── mem/
    └── digest_{channel}_{chat_id}.md  # Digest de memoria por scope
```

---

## 4. Entidades del Dominio

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
    tool_calls: list[dict] | None = None  # para rol assistant con calls
    tool_call_id: str | None = None        # para rol tool (resultado)
```

```python
# core/domain/entities/memory.py
class MemoryEntry(BaseModel):
    id: str                          # UUID
    content: str
    embedding: list[float]           # dimensión 384 (e5-small)
    relevance: float                 # 0.0–1.0, estimado por LLM extractor
    tags: list[str]
    created_at: datetime
    agent_id: str | None = None
    channel: str | None = None       # scope (channel, chat_id) del origen
    chat_id: str | None = None
    deleted: int = 0                 # soft-delete: 0 = activo, 1 = eliminado
```

```python
# core/domain/entities/task.py
class TaskType(str, Enum):
    RECURRENT = "recurrent"   # cron
    ONESHOT = "oneshot"       # datetime exacto

class TaskStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"

class ScheduledTask(BaseModel):
    id: str
    agent_id: str
    description: str
    task_kind: TaskType
    schedule: str                    # cron expr o ISO datetime
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
    categoria: str | None = None     # None = normal, "ignorada" = skip permanente
```

---

## 5. Puertos Clave

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

`LLMResponse` encapsula el texto y los `tool_calls` devueltos por el modelo.

### `IHistoryStore`

```python
class IHistoryStore(ABC):
    async def append(self, agent_id: str, message: Message, channel: str, chat_id: str) -> None: ...
    async def load(self, agent_id: str, channel: str, chat_id: str, limit: int) -> list[Message]: ...
    async def clear(self, agent_id: str, channel: str, chat_id: str) -> None: ...
    async def record_user_message(self, agent_id: str, message: Message, channel: str, chat_id: str) -> None: ...
    async def drain_pending(self, agent_id: str, channel: str, chat_id: str) -> list[Message]: ...
```

El historial se almacena en **SQLite** (`history.db`). Está scoped por `(agent_id, channel, chat_id)`. `record_user_message` + `drain_pending` soportan la inyección de mensajes in-flight.

### `IScopeRegistry`

```python
class IScopeRegistry(ABC):
    async def try_mark_busy(self, scope: Scope) -> bool: ...
    async def mark_idle(self, scope: Scope) -> None: ...
```

`Scope = tuple[str, str, str]` — `(agent_id, channel, chat_id)`. Implementado con `asyncio.Lock` en `InMemoryScopeRegistryAdapter`. Una sola instancia compartida entre todos los agentes (los scopes son disjuntos por `agent_id`).

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

Implementado por `InsightFaceAdapter`. El modelo se carga de forma **lazy** en la primera llamada (`_get_app()` con singleton perezoso). Ocupa ~400 MB de RAM.

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

Orquesta un turno completo de conversación:

1. Cargar historial scoped por `(agent_id, channel, chat_id)`
2. Si semantic routing activo: generar embedding del input y filtrar tools/skills relevantes via cosine similarity
3. Construir `AgentContext` y system prompt dinámico (base + memoria digest + skills)
4. Llamar al LLM vía `run_tool_loop()` — ver §7
5. Persistir mensajes `user` / `assistant` en historial (nunca `tool` ni `tool_result`)
6. Devolver `ChatTurnResult`

### `_tool_loop.run_tool_loop()`

Loop LLM ↔ tools hasta agotar `tool_call_max_iterations` (default 5) o hasta que el LLM no llame más tools:

- **Circuit breaker:** si la misma tool falla `circuit_breaker_threshold` veces consecutivas, se corta el loop.
- **In-flight injection:** entre iteraciones (checkpoints A: antes de `llm.complete`, B: después del batch de tool_calls), drena mensajes pendientes del usuario via `history_store.drain_pending()`. Si hay mensajes, resetea el contador de iteraciones a 0.
- Backward-compatible: `history_store=None` desactiva la inyección (modo legacy).

### `ConsolidateMemoryUseCase`

1. Carga el historial no-infundido del scope
2. LLM extrae recuerdos como JSON
3. Genera embedding para cada recuerdo (`embed_passage`)
4. Persiste en `IMemoryRepository` (DELETE + INSERT para evitar bug UNIQUE en `vec0`)
5. Si todo OK: archiva y limpia historial. Si falla: historial intacto (transaccional)

### `ProcessPhotoUseCase`

1. Descarga la imagen desde Telegram vía `IFileDownloader`
2. Llama a `IVisionPort.detect_and_embed()` → lista de `FaceDetection`
3. Para cada cara: busca en `IFaceRegistry` por cosine similarity
4. Según el resultado (MATCHED / AMBIGUOUS / UNKNOWN / IGNORED): decide si enrolar, ignorar o pedir confirmación
5. Llama a `ISceneDescriber.describe()` → descripción de texto de la escena
6. Persiste metadata en `IMessageFaceMetadataRepo` (side-table en `history.db`, `ON DELETE CASCADE`)
7. Devuelve respuesta combinada (reconocimientos + descripción)

### Config Use Cases (`core/use_cases/config/`)

CRUD de configuración YAML a través de `IConfigRepository`. Usados por la TUI y el admin REST. Operan sobre `~/.inaki/config/` sin tocar el repositorio.

---

## 7. Sistema de Configuración

### 4 capas de merge (campo a campo)

```
~/.inaki/config/global.yaml
        ↓ merge campo a campo
~/.inaki/config/global.secrets.yaml
        ↓ merge campo a campo
~/.inaki/config/agents/{id}.yaml
        ↓ merge campo a campo
~/.inaki/config/agents/{id}.secrets.yaml
        ↓
AgentConfig resuelto
```

- Cada capa solo sobreescribe los campos que define. Los campos ausentes se heredan.
- `*.secrets.yaml` están en `.gitignore` y **nunca se commitean**.
- La referencia canónica y comentada de todos los parámetros es `config/global.example.yaml`.

### Registry de providers

```yaml
providers:
  openrouter: { api_key: "sk-or-..." }
  groq:       { api_key: "gsk_..." }
  openai:     { api_key: "sk-..." }
```

`llm.provider`, `embedding.provider` y `transcription.provider` referencian una key de este dict. Las credenciales viven **solo** en el registry, nunca en los bloques feature.

### Modelos de config relevantes

```python
class GlobalConfig(BaseModel):
    app: AppConfig
    providers: dict[str, ProviderEntry]
    llm: LLMConfig
    embedding: EmbeddingConfig
    memory: MemoryConfig
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
    channels: dict[str, dict]   # telegram, rest, broadcast — por agente
```

---

## 8. Sistema Multi-Agente

### Arranque

`AppContainer.__init__()`:
1. Construye un `AgentContainer` por agente (primera pasada)
2. Registra la tool `delegate` en cada container con referencias a los demás (segunda pasada — necesaria porque los containers deben existir antes de las referencias cruzadas)
3. Inicia `SchedulerService` con todos los agentes

### Scope de historial y memoria

- **Historial:** scoped por `(agent_id, channel, chat_id)`. Conversaciones en grupos de Telegram, privados y CLI quedan completamente aisladas.
- **Memoria:** scoped por `(agent_id, channel, chat_id)` opcionalmente. `channel=NULL, chat_id=NULL` = recuerdos globales pre-migración.
- **Agent state** (sticky tools/skills): scoped por `(agent_id, channel, chat_id)`.

### Delegación

La tool `delegate` permite que un agente invoque a otro. Dos modos:

- **`wait=true`** — sincrónico (legacy): bloquea hasta recibir `DelegationResult`.
- **`wait=false`** — asíncrono (default): encola en `BackgroundDelegationQueueAdapter` (semáforo = 3), devuelve `bg-N` al instante. Cuando termina, el resultado se inyecta como `Role.USER` con prefijo `[bg-N]` en el scope origen via `LLMDispatcherAdapter`.

`LLMDispatcherAdapter` se construye **una sola vez** en `AppContainer` y se comparte entre el queue adapter y el `SchedulerService`. Esto serializa turnos sobre el mismo `(agent_id, channel, chat_id)` vía lock-per-scope.

### In-flight message injection

Cuando llega un mensaje nuevo sobre un scope que ya tiene un `execute()` en curso:

```
if try_mark_busy(scope):
    try: execute() finally: mark_idle(scope)
else:
    record_user_message(message)
    return "📝 incorporando a la tarea en curso..."
```

El tool loop drena esos mensajes entre iteraciones y los incorpora al contexto del LLM. Al recibir mensajes drenados, el contador de iteraciones se resetea. El circuit breaker **no** se resetea (los fallos de tools siguen acumulando).

**Grupos de Telegram excluidos**: el pipeline de grupo usa buffer+delay de coalescencia natural; la inyección in-flight no aplica allí.

---

## 9. Canales

### CLI

```bash
inaki                            # agente por defecto, interactivo
inaki chat --agent dev           # agente específico
inaki --remote http://host:6497  # conectarse a daemon remoto
```

### Telegram

Un bot por agente (un token por agente). Config en `agents/{id}.yaml`:

```yaml
channels:
  telegram:
    allowed_user_ids: ["123456789"]
    allowed_chat_ids: []           # grupos autorizados (IDs negativos)
    reactions: true
    voice_enabled: true            # transcripción Whisper
```

**Grupos:** pipeline con buffer + delay aleatorio + coalescencia de mensajes consecutivos del mismo autor. Los mensajes del grupo se coalesce antes de enviar al LLM para evitar alternación `user/assistant` rota.

**Broadcast multi-Pi:** ver §11.

### REST API

Una instancia FastAPI por agente en su propio puerto. Autenticación por `X-API-Key`. Endpoints: `GET /info`, `POST /chat`, `POST /consolidate`, `GET/DELETE /history`.

**Admin REST** (puerto 6497, `127.0.0.1`): server centralizado para el daemon. Expone endpoints de gestión (`/agents`, `/config`, `/chat` desde CLI remoto).

---

## 10. Semantic Routing

Tools y skills se seleccionan por cosine similarity (embedding del input vs. embedding de descripción/nombre):

- **Sin routing** (< `semantic_routing_min_tools`/`min_skills` tools/skills): se pasan todas al LLM.
- **Con routing**: se calculan scores, se pasan las top-K que superen `min_score`.
- **Sticky:** si el LLM usó una tool/skill en el turno anterior, se mantiene en el contexto durante `sticky_ttl` turnos aunque el routing no la incluiría. Implementado en `StickySelector`.

El estado sticky se persiste en `agent_state` en `history.db`, scoped por `(agent_id, channel, chat_id)`.

---

## 11. Broadcast Multi-Pi

Permite que múltiples instancias de Inaki en la misma LAN compartan un grupo de Telegram. La Bot API no entrega mensajes de otros bots, por lo que se usa un canal lateral TCP.

**Topología:** estrella — un server (`broadcast.port`), N clients (`broadcast.remote.host`).  
**Wire format:** JSON line-delimited, firmado HMAC-SHA256 con ventana de frescura de 60 s.  
**Modos de behavior:** `listen` (solo recibe) | `mention` (responde si lo mencionan) | `autonomous` (responde si considera que aporta, puede emitir `[SKIP]`).  
**Rate limiter:** `FixedWindowRateLimiter` para evitar loops infinitos en modo `autonomous`.

---

## 12. Features Opcionales

### Reconocimiento facial (fotos en Telegram)

Activado con `photos.enabled: true`. Pipeline:

1. `IVisionPort.detect_and_embed()` → `list[FaceDetection]` (InsightFace, lazy-load)
2. `IFaceRegistry` busca por embedding en `faces.db` (sqlite-vec FLOAT[512])
3. `ISceneDescriber.describe()` → descripción LLM multimodal

Base de datos `faces.db` independiente de `history.db` e `inaki.db`. La tabla `persons` usa `categoria VARCHAR`: `NULL` = persona normal, `"ignorada"` = ignorada permanentemente.

⚠ Cambiar `faces.model` invalida `faces.db` — borrar y re-enrolar.  
⚠ `schema_meta.embedding_dim` se valida al arrancar. Mismatch → `EmbeddingDimensionMismatchError`.

### Transcripción de voz

Activado con `channels.telegram.voice_enabled: true` (default). Usa `ITranscriptionProvider` (Groq Whisper). El proveedor se descubre dinámicamente igual que LLM y embedding.

### Knowledge Sources (RAG sobre documentos)

Configurado en `knowledge.sources`. Tres tipos: `document` (Markdown, PDF en disco), `sqlite` (tabla SQLite), `memory` (fusión con memorias del agente). El `KnowledgeOrchestrator` agrega resultados de todas las fuentes con presupuesto de tokens.

---

## 13. Scheduler

`SchedulerService` corre un loop async. Dos tipos de tareas:

- **RECURRENT** — expresión cron (`croniter`). Se dispara cuando `next_run_time <= now`.
- **ONESHOT** — datetime ISO exacto. Se dispara una vez y pasa a `DONE`.

Las tareas se persisten en `scheduler.db` (o en `history.db`, según config). El dispatcher (`SchedulerDispatchPorts`) enruta la ejecución según el tipo de tarea: a `LLMDispatcherAdapter`, a `ConsolidationDispatchAdapter` o a `HttpCallerAdapter`.

Tareas built-in registradas automáticamente: `consolidate_memory` (nightly, cron desde `memory.schedule`) y `face_dedup` (si `photos.dedup.enabled`).

---

## 14. Extensiones (`ext/`)

Mecanismo de auto-descubrimiento: cualquier carpeta en `ext/` con un `manifest.py` que declare el package se carga automáticamente. Las tools que implementen `ITool` y las skills YAML que sigan la convención se registran sin tocar nada en `core/` ni `infrastructure/`.

Extensiones incluidas: `exchange_calendar`, `nominatim`, `notes_todo_list`, `replicate_music`, `shell_exec`.

Convenciones:

| Elemento | Convención |
|---|---|
| Archivo tool | `{nombre}_tool.py` |
| Clase tool | `{Nombre}Tool` |
| `ITool.name` | `snake_case` |
| Skill | `{nombre}.yaml` con campos `name`, `description`, `content` |

---

## 15. Provider Factories (Descubrimiento Dinámico)

Las factories escanean sus carpetas, importan módulos, leen `PROVIDER_NAME` y construyen un registry en memoria. Añadir un proveedor = crear el fichero con `PROVIDER_NAME` correcto. Sin tocar nada más.

Aplica a: `adapters/outbound/providers/` (LLM), `adapters/outbound/embedding/` (embedding), `adapters/outbound/transcription/` (voz).

```python
# Convención obligatoria
PROVIDER_NAME = "mi_proveedor"

class MiProvider(BaseLLMProvider):
    ...
```

---

## 16. Testing

- `pytest-asyncio` en modo `auto` — sin `@pytest.mark.asyncio`.
- Fixtures compartidos en `tests/conftest.py`: `agent_config` (`:memory:` DB), `mock_llm`, `mock_memory`, `mock_embedder`, `mock_skills`, `mock_history`, `mock_tools`.
- Tests unitarios: mocks de todos los puertos. Sin SQLite, ONNX ni red.
- Tests de integración: SQLite real en memoria o archivo temporal.

```bash
pytest                          # todos
pytest tests/unit/              # solo unitarios
pytest tests/integration/       # solo integración
pytest -k test_name             # test específico
```

---

## 17. Gestión de Errores

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

Los adaptadores loggean en su capa y propagan excepciones tipadas hacia arriba. El core nunca loggea directamente — usa excepciones para comunicar errores.

---

## 18. Reglas de Desarrollo

Al añadir cualquier nueva funcionalidad, este es el orden obligatorio:

1. **Entidad/Value Object** en `core/domain/` si se introduce un nuevo concepto
2. **Puerto** en `core/ports/` si se necesita una nueva dependencia externa
3. **Use Case** en `core/use_cases/` con la orquestación
4. **Test unitario** en `tests/unit/` con mocks de los puertos — antes del adaptador
5. **Adaptador** en `adapters/outbound/` o `adapters/inbound/`
6. **Wiring** en `infrastructure/container.py`
7. **Config** en `config/global.example.yaml` si requiere nuevos parámetros

**Nunca saltarse pasos. Nunca mezclar capas.**

---

*Versión: 2.x — Actualizado para reflejar el sistema completo post-`in-flight-message-injection`.*
