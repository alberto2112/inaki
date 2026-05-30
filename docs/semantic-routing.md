# Inaki's Semantic Routing Mechanism

Documentation of the **semantic routing** pipeline for dynamic skill and tool selection.

> **Note**: this mechanism is NOT RAG. It selects available capabilities (skills/tools) by semantic similarity with the query. The actual RAG — retrieval of external knowledge (documents, databases) to inject into the prompt — lives in `knowledge:` and is documented separately.

---

## What Is It and What Is It For?

When the agent has many available skills or tools, sending all of them to the LLM on every turn is costly and inefficient. Semantic routing solves this problem: instead of sending the full list, it generates an embedding of the user's query and selects only the most semantically similar skills/tools.

The result: the LLM receives a smaller and more precise context, which improves response quality and reduces tokens.

---

## General Flow

```
User writes query
        │
        ▼
RunAgentUseCase.execute()
        │
        ├── list_all_skills()     → more than semantic_routing_min_skills? → skills_routing_active
        ├── get_all_schemas()     → more than semantic_routing_min_tools?  → tools_routing_active
        │
        ├── short input (< semantic_routing.min_words_threshold) AND previous sticky exists?
        │       ├── YES → short-input bypass:
        │       │         inherit previous sticky selection intact
        │       │         (no embed, no TTL decay, no persist)
        │       └── NO → follow normal flow:
        │                 └── embed_query(user_input) → query_vec
        │
        ├── (if skills_routing_active and NOT bypass)
        │       └── skills.retrieve(query_vec, top_k, min_score) → retrieved_skills
        │
        ├── (if tools_routing_active and NOT bypass)
        │       └── tools.get_schemas_relevant(query_vec, top_k, min_score) → tool_schemas
        │
        ▼
AgentContext(skills=retrieved_skills)
        │
        └── build_system_prompt() → system prompt with only the relevant skills
```

The query embedding is generated **once** even if both routings are active.

---

## Skill Selection Flow

```
add_file(path) → YamlSkillRepository._extra_files[]

list_all() / retrieve()
        │
        └── _ensure_loaded()
                │
                ├── for each YAML file:
                │       │
                │       ├── read_bytes() → raw_bytes
                │       ├── md5(raw_bytes) → content_hash
                │       │
                │       ├── cache.get(content_hash, provider, dimension)
                │       │       ├── HIT  → embedding from cache (without calling the model)
                │       │       └── MISS → embedder.embed_passage(name + desc + tags)
                │       │                       └── cache.put(content_hash, ...)
                │       │
                │       └── append(skill, embedding)
                │
                └── _loaded = True  (not reloaded until a new add_file)

retrieve(query_vec, top_k, min_score)
        │
        ├── cosine_similarity(query_vec, emb) for each skill
        ├── sort desc by score
        ├── filter score < min_score (if min_score > 0.0)
        └── top_k skills → inject into system prompt
```

**Hash key**: the MD5 hash is computed over the raw bytes of the entire YAML file, not over individual fields. If the file changes (any field), the hash changes and the cached embedding is discarded.

---

## Tool Selection Flow

```
register(tool) → ToolRegistry._tools{}
                   └── _embeddings_ready = False (invalidate)

get_schemas_relevant(query_vec, top_k, min_score)
        │
        └── _ensure_embeddings()
                │
                ├── for each tool not yet embedded:
                │       │
                │       ├── md5(tool.description.encode()) → content_hash
                │       │
                │       ├── cache.get(content_hash, provider, dimension)
                │       │       ├── HIT  → embedding from cache
                │       │       └── MISS → embedder.embed_passage(description)
                │       │                       └── cache.put(content_hash, ...)
                │       │
                │       └── _embeddings[tool.name] = embedding
                │
                └── _embeddings_ready = True

scored = cosine_similarity(query_vec, emb) for each tool
filter score < min_score (if min_score > 0.0)
top_k names → schemas for those tools
```

**Key difference from skills**: a tool's hash is computed over `tool.description` (string), not over file bytes. If the description doesn't change between restarts, the embedding is reused from cache.

---

## Cosine Similarity

Implemented in `core/domain/services/similarity.py`:

```
cos_sim(a, b) = dot(a, b) / (||a|| * ||b||)
```

- Uses `numpy` internally (float32)
- Returns 0.0 if either vector has zero norm (null vector)
- Scale: -1.0 (opposite) → 0.0 (orthogonal) → 1.0 (identical)
- Configurable threshold: `semantic_routing_min_score` filters results below the threshold before applying top_k (default 0.0 = no filter)

---

## Embedding Cache

### Port (interface)

`core/ports/outbound/embedding_cache_port.py` defines `IEmbeddingCache`:

```
get(content_hash, provider, dimension) → list[float] | None
put(content_hash, provider, dimension, embedding) → None
```

### SQLite Implementation

`adapters/outbound/embedding/sqlite_embedding_cache.py` — `SqliteEmbeddingCache`:

**Table schema:**

```sql
CREATE TABLE embedding_cache (
    content_hash  TEXT    NOT NULL,
    provider      TEXT    NOT NULL,
    dimension     INTEGER NOT NULL,
    embedding     TEXT    NOT NULL,   -- JSON serialized: "[0.1, 0.2, ...]"
    created_at    TEXT    NOT NULL,
    PRIMARY KEY (content_hash, provider, dimension)
);
```

**Triple composite key**: `(content_hash, provider, dimension)`. This allows:
- Changing embedding providers (e.g. from `e5_onnx` to `openai`) without conflicts
- Changing the model dimension without conflicts
- Multiple configurations coexisting in the same `.db` file

**Hit/miss behavior:**

```
get(hash, provider, dim)
    ├── HIT  → deserialize JSON → list[float] (without calling the model)
    └── MISS → None

put(hash, provider, dim, embedding)
    └── INSERT OR REPLACE (upsert)
```

**Robustness**: SQLite errors in `get()` return `None` (miss behavior). Errors in `put()` are logged as WARNING but do not propagate exceptions. The system operates in degraded mode if the cache fails.

**WAL mode**: the connection uses `PRAGMA journal_mode=WAL` for better concurrency on simultaneous reads.

**Note**: the cache is **optional**. Both `YamlSkillRepository` and `ToolRegistry` accept `cache=None`, in which case they always call the embedder.

---

## Configuration

### `EmbeddingConfig` (in `infrastructure/config.py`)

| Field | Default | Description |
|-------|---------|-------------|
| `provider` | `"e5_onnx"` | Embedding provider |
| `model_dirname` | `"models/e5-small"` | ONNX model directory (relative to `~/.inaki/`, e5_onnx only) |
| `model` | `"text-embedding-3-small"` | Model name (openai only) |
| `dimension` | `384` | Embedding vector dimension |
| `cache_filename` | `"data/embedding_cache.db"` | SQLite cache file (relative to `~/.inaki/`) |

### `SkillsConfig`

| Field | Default | Description |
|-------|---------|-------------|
| `semantic_routing_min_skills` | `10` | Minimum skills to activate routing. With 10 or fewer skills, all are sent |
| `semantic_routing_top_k` | `3` | How many skills the retrieve returns |
| `semantic_routing_min_score` | `0.0` | Minimum cosine similarity score (0.0-1.0). Skills below this are discarded BEFORE applying top_k. 0.0 = no filter |

### `ToolsConfig`

| Field | Default | Description |
|-------|---------|-------------|
| `semantic_routing_min_tools` | `10` | Minimum tools to activate routing |
| `semantic_routing_top_k` | `5` | How many tools the retrieve returns |
| `semantic_routing_min_score` | `0.0` | Minimum cosine similarity score (0.0-1.0). Tools below this are discarded BEFORE applying top_k. 0.0 = no filter |
| `tool_call_max_iterations` | `5` | Maximum tool loop iterations |
| `circuit_breaker_threshold` | `2` | Consecutive failures before cutting off |

### `SemanticRoutingConfig`

| Field | Default | Description |
|-------|---------|-------------|
| `min_words_threshold` | `0` | Minimum words in user_input to re-run routing. Below this threshold (and if a previous sticky exists) embedding is skipped and the previous turn's selection is inherited intact. `0` = feature disabled (historical behavior) |

---

## Word Count Gate (short-input bypass)

Parameter: `semantic_routing.min_words_threshold` (see `SemanticRoutingConfig`).

**Motivation.** In short follow-ups — "yes", "go ahead", "and that?" — the embedding has little semantic signal and usually the context is still the same as the previous turn. Recomputing routing on every short turn (a) spends an embedder call and (b) can "reset" relevant skills/tools that were already selected.

**Semantics.** At the start of `execute()` the following is evaluated:

```
is_short = (
    semantic_routing.min_words_threshold > 0
    and len(user_input.split()) < semantic_routing.min_words_threshold
    and (prev_state.sticky_skills or prev_state.sticky_tools)
)
```

If `is_short` is `True`:

- `embed_query` is NOT called → saves latency and embedder quota
- `apply_sticky` is NOT run → the sticky TTL stays **frozen** (does not decrement)
- `retrieved_skills` / `tool_schemas` are reconstructed from `prev_state.sticky_*` (filtering ids that no longer exist in the current catalog)
- `state_dirty = False` → state is NOT persisted

If `is_short` is `False`, the original pipeline runs without changes.

**Edge cases.**

- First turn (empty sticky) with short input → routing **runs normally**. Without a previous sticky there's no context to inherit from.
- Routing deactivated by pool thresholds (`semantic_routing_min_skills` / `semantic_routing_min_tools` not exceeded) → irrelevant, all skills/tools were already being sent.
- `tools_override` active (e.g. scheduler `agent_send`) → the override always takes precedence; the gate only affects skills.
- Strict threshold: an input with exactly `min_words_threshold` words is **not** short (comparison is `<`, not `<=`).

**Intent.** Policy of the routing caller, not of the embedder. `EmbeddingConfig` describes "how an embedding is computed"; `SemanticRoutingConfig` describes "when to activate the routing pipeline". That's why it doesn't live inside `EmbeddingConfig`.

**Visibility.** `inspect()` applies the same gate as `execute()` — if the input is short and there's a previous sticky, it shows the inherited selection (not what would result from re-running routing). This way the debug reflects what the LLM would actually see.

---

## Skill Injection into the System Prompt

`AgentContext.build_system_prompt()` in `core/domain/value_objects/agent_context.py`:

```
system_prompt = base_prompt
    + memory_digest (if it exists)
    + "## Available skills:\n\n### Name\ndescription\n\ninstructions"
    + extra_sections (e.g. agent discovery for delegation)
```

Each skill is rendered as a markdown block with a `###` heading, description as the first paragraph, and instructions separated by a blank line:

```markdown
## Available skills:

### Web Search
Searches for information on the internet using DuckDuckGo

When the user asks about current events or needs information...

### Calculator
Performs mathematical calculations

Use this skill when the user asks for numeric operations...
```

Only skills retrieved by routing (or all if routing is inactive) appear in the prompt.

---

## Hexagonal Architecture

| Layer | File | Role |
|-------|------|------|
| **Core — Port** | `core/ports/outbound/embedding_cache_port.py` | `IEmbeddingCache` interface |
| **Core — Port** | `core/ports/outbound/embedding_port.py` | `IEmbeddingProvider` interface |
| **Core — Port** | `core/ports/outbound/skill_port.py` | `ISkillRepository` interface |
| **Core — Service** | `core/domain/services/similarity.py` | `cosine_similarity` function |
| **Core — Value Object** | `core/domain/value_objects/agent_context.py` | System prompt construction |
| **Core — Use Case** | `core/use_cases/run_agent.py` | Routing pipeline orchestration |
| **Adapter** | `adapters/outbound/embedding/sqlite_embedding_cache.py` | SQLite cache implementation |
| **Adapter** | `adapters/outbound/skills/yaml_skill_repo.py` | Skill loading + routing |
| **Adapter** | `adapters/outbound/tools/tool_registry.py` | Tool registration + routing |
| **Infrastructure** | `infrastructure/container.py` | Wiring: instantiates and connects everything |
| **Config** | `infrastructure/config.py` | `EmbeddingConfig`, `SkillsConfig`, `ToolsConfig`, `SemanticRoutingConfig` |

The hexagonal rule is respected: the core doesn't know about SQLite or YAML. It only depends on the interfaces (`IEmbeddingCache`, `IEmbeddingProvider`, `ISkillRepository`).
