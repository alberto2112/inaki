# Data Model — Inaki v2

All domain entities live in `core/domain/` and are **Pydantic BaseModel**.
The core never imports from `adapters/` or infrastructure libraries.

---

## Entities (`core/domain/entities/`)

### `Message` — `message.py`

Conversation unit. History is a `list[Message]`.

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
    timestamp: datetime | None = None   # UTC; assigned by SQLiteHistoryStore in append()
```

> **Critical rule:** the history persisted in SQLite only contains `USER` and `ASSISTANT`.
> `TOOL` and `TOOL_RESULT` messages are ephemeral — they only live during the tool loop.
> `timestamp` is mutated in `append()` if `None`, assigning `datetime.now(UTC)`. It flows through to `MemoryEntry.created_at` during consolidation.

---

### `MemoryEntry` — `memory.py`

Long-term memory entry extracted by `ConsolidateMemoryUseCase`.

```python
class MemoryEntry(BaseModel):
    id: str               # Auto-generated UUID
    content: str          # Memory text ("Likes Python")
    embedding: list[float] # 384d vector generated with embed_passage()
    relevance: float      # 0.0–1.0, estimated by the LLM extractor
    tags: list[str]       # Semantic tags ["tech", "preferences"]
    created_at: datetime  # UTC — comes from the original message timestamp, not from when consolidation ran
    agent_id: str | None = None   # Scoped per agent
    channel: str | None = None    # Scope: channel of origin (e.g. "telegram", "cli")
    chat_id: str | None = None    # Scope: chat_id of origin; NULL = pre-migration global
    deleted: int = 0              # Soft-delete: 0 = active, 1 = deleted (reversible)
    reconciled: int = 0           # 0 = pending reconciliation, 1 = already processed
```

> Memory is **scoped** by `(agent_id, channel, chat_id)`. Entries with `channel=NULL, chat_id=NULL` are pre-migration globals and are still searchable. `search()`, `get_recent()` and `search_with_scores()` always filter `deleted=0`. `reconciled=0` marks entries pending reconciliation; entries created by a `merge` action are born with `reconciled=1` to prevent re-processing until new neighbors appear.
> `created_at` reflects when the fact occurred in conversation — the LLM extractor includes it in the JSON as a `timestamp` field.

**History SQLite schema** (`data/history.db`):
```sql
CREATE TABLE history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT    NOT NULL,
    channel    TEXT    NOT NULL DEFAULT '',  -- channel type ("telegram", "cli", ...)
    chat_id    TEXT    NOT NULL DEFAULT '',  -- channel-specific conversation id
    role       TEXT    NOT NULL,             -- "user" | "assistant"
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,             -- ISO8601 UTC
    archived   INTEGER NOT NULL DEFAULT 0,  -- LEGACY: always 0, kept for compat
    infused    INTEGER NOT NULL DEFAULT 0   -- 0=pending consolidation, 1=already processed
);
CREATE INDEX idx_history_scope ON history(agent_id, channel, chat_id, archived);
```

History is **scoped by `(agent_id, channel, chat_id)`** — Telegram groups, private chats and CLI are completely isolated. `clear()` performs a hard-delete for the scope. `trim()` (post-consolidation) deletes all but the last N messages. `archived` is legacy and always 0; `infused` gates re-extraction in consolidation. Separate database from `data/inaki.db` to avoid interfering with the `sqlite-vec` extension.

---

**Memory SQLite schema** (`data/inaki.db`):
```sql
CREATE TABLE memories (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    relevance  REAL NOT NULL,
    tags       TEXT NOT NULL,     -- Serialized JSON array
    created_at TEXT NOT NULL,     -- ISO 8601
    agent_id   TEXT,              -- Scoped per agent
    channel    TEXT,              -- Scope: channel of origin; NULL = pre-migration global
    chat_id    TEXT,              -- Scope: chat_id of origin; NULL = pre-migration global
    deleted    INTEGER NOT NULL DEFAULT 0,  -- Soft-delete: 0=active, 1=deleted
    reconciled INTEGER NOT NULL DEFAULT 0   -- 0=pending reconciliation, 1=already processed
);

CREATE INDEX idx_memories_scope
    ON memories(agent_id, channel, chat_id, created_at DESC)
    WHERE deleted = 0;  -- partial index over active entries only

CREATE INDEX idx_memories_unreconciled
    ON memories(agent_id)
    WHERE reconciled = 0 AND deleted = 0;  -- partial index for reconciliation seed lookup

CREATE VIRTUAL TABLE memory_embeddings USING vec0(
    id        TEXT PRIMARY KEY,
    embedding FLOAT[384]          -- sqlite-vec KNN
);
```

---

### `Skill` — `skill.py`

Specialized capability or knowledge loaded from YAML.

```python
class Skill(BaseModel):
    id: str
    name: str
    description: str
    instructions: str = ""   # Detailed instructions for the LLM
    tags: list[str] = []

class SkillResult(BaseModel):
    skill_id: str
    applied: bool
    notes: str = ""
```

**YAML format for a skill** (`skills/*.yaml`):
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

Task scheduled for future or periodic execution.

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"

class TaskType(str, Enum):
    CRON = "cron"    # Cron expression ("0 8 * * *")
    ONCE = "once"    # ISO datetime ("2024-12-25T09:00:00")

class ScheduledTask(BaseModel):
    id: str               # UUID
    agent_id: str
    name: str
    description: str
    task_type: TaskType
    schedule: str         # cron expression or ISO datetime
    prompt: str           # Message to send to the agent when it fires
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
    vector: list[float]   # 384 dimensions for e5-small
    model: str            # "e5_onnx" or another provider
```

---

### `AgentContext` — `agent_context.py`

Per-turn context state. Not persisted — built fresh on each `execute()`.

```python
class AgentContext(BaseModel):
    agent_id: str
    memories: list[MemoryEntry] = []   # Retrieved via vector search
    skills: list[Skill] = []           # Retrieved via cosine similarity

    def build_system_prompt(self, base_prompt: str) -> str:
        """
        Builds the system prompt injecting memory and skills.

        Result:
          {base_prompt}

          ## What you remember about the user:
          - Likes Python
          - Prefers concise answers

          ## Available skills:
          - **Web Search**: Searches for up-to-date information...
            When the user asks about current events...
        """
```

---

## Error Hierarchy (`core/domain/errors.py`)

```
InakiError
├── AgentNotFoundError     # Agent does not exist in the registry
├── LLMError               # Error calling the LLM provider
├── ConsolidationError     # Error during consolidation (history guaranteed intact)
├── EmbeddingError         # Error generating embeddings
├── ToolError              # Error executing a tool
└── HistoryError           # Error reading/writing history
```

---

## Ports (`core/ports/`)

Ports are contracts (ABC) that the core defines and adapters implement.

### Outbound (what the core needs from the outside)

| Port | File | Main Methods |
|------|------|--------------|
| `ILLMProvider` | `llm_port.py` | `complete(messages, system_prompt, tools?)`, `stream(...)` |
| `IMemoryRepository` | `memory_port.py` | `store(entry)`, `search(embedding, top_k)`, `get_recent(limit)` |
| `IEmbeddingProvider` | `embedding_port.py` | `embed_query(text)`, `embed_passage(text)` |
| `IToolExecutor` | `tool_port.py` | `register(tool)`, `execute(name, **kwargs)`, `get_schemas()` |
| `ISkillRepository` | `skill_port.py` | `retrieve(embedding, top_k)` |
| `IHistoryStore` | `history_port.py` | `append(agent_id, msg)`, `load(agent_id)`, `load_full(agent_id)`, `archive(agent_id)`, `clear(agent_id)` |

### Inbound (what the core exposes to the outside)

| Port | File | Methods |
|------|------|---------|
| `IAgentUseCase` | `agent_port.py` | `execute(agent_id, user_input) -> str` |
| `ISchedulerUseCase` | `scheduler_port.py` | `schedule(task)`, `cancel(task_id)`, `list_tasks(agent_id)` |

---

## `ToolResult` — tool execution result

```python
class ToolResult(BaseModel):
    tool_name: str
    output: str      # Verbatim output from the tool
    success: bool
    error: str | None
```

---

## Relationships Between Models

```
AgentConfig ──────────────────► AgentContainer
    │                               │
    ├── LLMConfig ──────────────► ILLMProvider
    ├── EmbeddingConfig ─────────► IEmbeddingProvider
    ├── MemoriesConfig ──────────► IMemoryRepository
    ├── ChatHistoryConfig ───────► IHistoryStore
    └── channels: dict           Tools: IToolExecutor
                                 Skills: ISkillRepository

AgentContainer
    ├── run_agent: RunAgentUseCase
    │       └── produce AgentContext (ephemeral, per-turn)
    │               └── build_system_prompt(base) → str
    └── consolidate_memory: ConsolidateMemoryUseCase
            └── produce MemoryEntry[] → stored in IMemoryRepository
```
