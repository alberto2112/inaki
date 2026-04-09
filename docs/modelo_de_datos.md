# Modelo de Datos — Iñaki v2

Todas las entidades del dominio están en `core/domain/` y son **Pydantic BaseModel**.
El core no importa de `adapters/` ni de librerías de infraestructura.

---

## Entidades (`core/domain/entities/`)

### `Message` — `message.py`

Unidad de conversación. El historial es una `list[Message]`.

```python
class Role(str, Enum):
    USER      = "user"
    ASSISTANT = "assistant"
    SYSTEM    = "system"
    TOOL      = "tool"
    TOOL_RESULT = "tool_result"

class Message(BaseModel):
    role: Role
    content: str
    timestamp: datetime | None = None   # UTC; asignado por SQLiteHistoryStore en append()
```

> **Regla crítica:** el historial persistido en SQLite solo contiene `USER` y `ASSISTANT`.
> Los mensajes `TOOL` y `TOOL_RESULT` son efímeros — viven solo durante el loop de tools.
> `timestamp` se muta en `append()` si es `None`, asignando `datetime.now(UTC)`. Fluye hasta `MemoryEntry.created_at` en la consolidación.

---

### `MemoryEntry` — `memory.py`

Recuerdo a largo plazo extraído por `ConsolidateMemoryUseCase`.

```python
class MemoryEntry(BaseModel):
    id: str               # UUID autogenerado
    content: str          # Texto del recuerdo ("Le gusta Python")
    embedding: list[float] # Vector 384d generado con embed_passage()
    relevance: float      # 0.0–1.0, estimada por el LLM extractor
    tags: list[str]       # Etiquetas semánticas ["tech", "preferencias"]
    created_at: datetime  # UTC — viene del timestamp del mensaje original, no de cuando se consolidó
    agent_id: str | None  # None = recuerdo global compartido entre todos los agentes
```

> La memoria es **global y compartida**: `agent_id = None` en todos los recuerdos.
> El historial de conversación es privado por agente.
> `created_at` refleja cuándo ocurrió el hecho en la conversación — el LLM extractor lo incluye en el JSON como campo `timestamp`.

**Schema SQLite de historial** (`data/history.db`):
```sql
CREATE TABLE history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT    NOT NULL,
    role       TEXT    NOT NULL,       -- "user" | "assistant"
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,       -- ISO8601 UTC
    archived   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_history_agent ON history(agent_id, archived);
```

`archive()` hace soft-delete (`archived=1`). `clear()` hace hard-delete. Base de datos separada de `data/inaki.db` para no interferir con la extensión `sqlite-vec`.

---

**Schema SQLite de memoria** (`data/inaki.db`):
```sql
CREATE TABLE memories (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    relevance  REAL NOT NULL,
    tags       TEXT NOT NULL,     -- JSON array serializado
    created_at TEXT NOT NULL,     -- ISO 8601
    agent_id   TEXT               -- NULL = global
);

CREATE VIRTUAL TABLE memory_embeddings USING vec0(
    id        TEXT PRIMARY KEY,
    embedding FLOAT[384]          -- sqlite-vec KNN
);
```

---

### `Skill` — `skill.py`

Capacidad o conocimiento especializado cargado desde YAML.

```python
class Skill(BaseModel):
    id: str
    name: str
    description: str
    instructions: str = ""   # Instrucciones detalladas para el LLM
    tags: list[str] = []

class SkillResult(BaseModel):
    skill_id: str
    applied: bool
    notes: str = ""
```

**Formato YAML de una skill** (`skills/*.yaml`):
```yaml
id: "web_search"
name: "Búsqueda Web"
description: "Busca información actualizada en internet"
instructions: |
  Cuando el usuario pregunte sobre eventos actuales...
tags:
  - "búsqueda"
  - "internet"
```

---

### `ScheduledTask` — `task.py`

Tarea programada para ejecución futura o periódica.

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"

class TaskType(str, Enum):
    CRON = "cron"    # Expresión cron ("0 8 * * *")
    ONCE = "once"    # ISO datetime ("2024-12-25T09:00:00")

class ScheduledTask(BaseModel):
    id: str               # UUID
    agent_id: str
    name: str
    description: str
    task_type: TaskType
    schedule: str         # cron expression o ISO datetime
    prompt: str           # Mensaje a enviar al agente cuando dispare
    status: TaskStatus    # default: PENDING
    created_at: datetime
    last_run: datetime | None
    next_run: datetime | None
```

---

## Value Objects (`core/domain/value_objects/`)

### `Embedding` — `embedding.py`

```python
class Embedding(BaseModel):
    vector: list[float]   # 384 dimensiones para e5-small
    model: str            # "e5_onnx" u otro provider
```

---

### `AgentContext` — `agent_context.py`

Estado de contexto ensamblado por turno. No se persiste — se construye en cada `execute()`.

```python
class AgentContext(BaseModel):
    agent_id: str
    memories: list[MemoryEntry] = []   # Recuperados via RAG
    skills: list[Skill] = []           # Recuperados via cosine similarity

    def build_system_prompt(self, base_prompt: str) -> str:
        """
        Construye el system prompt inyectando memoria y skills.

        Resultado:
          {base_prompt}

          ## Lo que recuerdas del usuario:
          - Le gusta Python
          - Prefiere respuestas concisas

          ## Skills disponibles:
          - **Búsqueda Web**: Busca información actualizada...
            Cuando el usuario pregunte sobre eventos actuales...
        """
```

---

## Jerarquía de errores (`core/domain/errors.py`)

```
IñakiError
├── AgentNotFoundError     # Agente no existe en el registry
├── LLMError               # Error al llamar al proveedor LLM
├── ConsolidationError     # Error durante consolidación (historial intacto garantizado)
├── EmbeddingError         # Error al generar embeddings
├── ToolError              # Error al ejecutar una tool
└── HistoryError           # Error al leer/escribir el historial
```

---

## Puertos (`core/ports/`)

Los puertos son contratos (ABC) que el core define y los adaptadores implementan.

### Outbound (lo que el core necesita del exterior)

| Puerto | Archivo | Métodos principales |
|--------|---------|---------------------|
| `ILLMProvider` | `llm_port.py` | `complete(messages, system_prompt, tools?)`, `stream(...)` |
| `IMemoryRepository` | `memory_port.py` | `store(entry)`, `search(embedding, top_k)`, `get_recent(limit)` |
| `IEmbeddingProvider` | `embedding_port.py` | `embed_query(text)`, `embed_passage(text)` |
| `IToolExecutor` | `tool_port.py` | `register(tool)`, `execute(name, **kwargs)`, `get_schemas()` |
| `ISkillRepository` | `skill_port.py` | `retrieve(embedding, top_k)` |
| `IHistoryStore` | `history_port.py` | `append(agent_id, msg)`, `load(agent_id)`, `load_full(agent_id)`, `archive(agent_id)`, `clear(agent_id)` |

### Inbound (lo que el core expone al exterior)

| Puerto | Archivo | Métodos |
|--------|---------|---------|
| `IAgentUseCase` | `agent_port.py` | `execute(agent_id, user_input) -> str` |
| `ISchedulerUseCase` | `scheduler_port.py` | `schedule(task)`, `cancel(task_id)`, `list_tasks(agent_id)` |

---

## `ToolResult` — resultado de ejecución de tool

```python
class ToolResult(BaseModel):
    tool_name: str
    output: str      # Output verbatim de la tool
    success: bool
    error: str | None
```

---

## Relaciones entre modelos

```
AgentConfig ──────────────────► AgentContainer
    │                               │
    ├── LLMConfig ──────────────► ILLMProvider
    ├── EmbeddingConfig ─────────► IEmbeddingProvider
    ├── MemoryConfig ────────────► IMemoryRepository
    ├── HistoryConfig ───────────► IHistoryStore
    └── channels: dict           Tools: IToolExecutor
                                 Skills: ISkillRepository

AgentContainer
    ├── run_agent: RunAgentUseCase
    │       └── produce AgentContext (ephemeral, per-turn)
    │               └── build_system_prompt(base) → str
    └── consolidate_memory: ConsolidateMemoryUseCase
            └── produce MemoryEntry[] → stored in IMemoryRepository
```
