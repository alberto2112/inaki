# Configuration — Inaki v2

## Interactive editing with `inaki setup`

The recommended way to edit the configuration is through the interactive TUI:

```bash
inaki setup          # opens the TUI (no subcommand)
inaki setup tui      # same, explicit
```

The TUI is **offline-only** — it does not require the daemon to be running. It reads and writes
directly to `~/.inaki/config/` using `ruamel.yaml`, preserving comments and
original formatting of YAML files.

### Navigation (V2 — keyboard-first)

The V2 TUI uses a **one page per category** architecture with linear navigation:

```
MainMenuPage → GlobalPage / AgentsPage / ProvidersPage / SecretsPage
AgentsPage   → AgentDetailPage (per agent)
```

| Key | Action |
|-----|--------|
| `↑` / `k` | move up one row |
| `↓` / `j` | move down one row |
| `Enter` | open edit modal for the selected field |
| `Esc` | go back to the previous screen |
| `q` | quit |
| `?` | quick help |

There is no mouse navigation as a primary flow.

### What the TUI can edit

| Screen | What it edits |
|--------|---------------|
| **GlobalPage** | All sections of `global.yaml` in a continuous form (a list of sections with their fields, no sub-screens) |
| **ProvidersPage** | Add/remove/edit `providers.*` in `global.yaml`; `api_key` always goes to `global.secrets.yaml` |
| **AgentsPage** | Create, clone, delete agents |
| **AgentDetailPage** | Per-agent overrides (same sections as GlobalPage, at the agent layer) |
| **SecretsPage** | Consolidated view of all `*.secrets.yaml` files; masked fields; individual reveal |

### Modal editing — one modal per field type

Every edit is done 100% in a modal — no inline editing. Four modal types:

| Field type | Modal | Keys |
|------------|-------|------|
| `scalar` (str, int, float) | `EditScalarModal` — Input with pre-filled value | `Enter` saves, `Esc` cancels |
| `enum` (Literal) | `EditEnumModal` — ListView with options | `↑↓ + Enter`, `Esc` cancels |
| `long` (system_prompt, description) | `EditLongModal` — TextArea | `Ctrl+S` saves, `Esc` cancels |
| `secret` (api_key, token, auth) | `EditSecretModal` — Input password=True | `Enter` saves, `Esc` cancels |

Current values are **pre-filled** in the modal. If the field is empty, the
Pydantic default is shown dimmed as a reference.

### Escape hatch `<null>`

In any `scalar` or `long` modal, typing `<null>` and confirming saves
the field as `null` in YAML. Useful for disabling an inherited value (e.g.
`llm.reasoning_effort: null`).

### Tri-state for `memory.llm.*`

The `memory.llm` fields on an agent have three modes — accessible from a
specialized modal by pressing `Enter` on any field in that section:

| State | Agent YAML | Meaning |
|-------|------------|---------|
| **Inherit** | field absent | uses the value from `memory.llm.*` in global |
| **Own value** | `memory.llm.field: value` | explicit agent value |
| **Null override** | `memory.llm.field: null` | overrides the inherited value with None |

Affected fields: `provider`, `model`, `temperature`, `max_tokens`, `reasoning_effort`.

### Cross-ref validation on save

After saving any field, the TUI validates cross-references:

- `app.default_agent` points to an agent that exists
- `llm.provider`, `embedding.provider`, `memory.llm.provider` point to registered providers

If there's an issue, a warning notification is shown **but the change is preserved** —
the TUI does not undo the save. The operator must correct the field in question.

### Web interface (V2)

```bash
inaki setup webui   # prints "Coming soon" and exits
```

The webui is pending for a future version. For now, use the TUI.

### Post-edit note

Changes in the TUI are written to disk atomically. However, the daemon
**does not reload the config automatically** — if the daemon is running, restart it:

```bash
systemctl restart inaki   # Pi 5 with systemd
# or
inaki daemon              # if running in foreground
```

### Functionality not available in the TUI (V2)

- `knowledge.sources` — RAG sources are edited manually in `global.yaml` for now.
- Live `api_key` validation — the TUI does not connect to providers to verify that the key is valid.
- Log viewing or daemon status — for that use `journalctl -u inaki` or `inaki inspect`.

---

## 4-layer merge system

The final configuration for each agent is built by merging four files in order.
Each layer overrides only the fields it defines — it never removes inherited fields that are absent.

```
config/global.yaml                 (1) system base config
    ↓ field-by-field merge
config/global.secrets.yaml         (2) global secrets (shared api keys)
    ↓ field-by-field merge
config/agents/{id}.yaml            (3) agent config (channels, model, prompt)
    ↓ field-by-field merge
config/agents/{id}.secrets.yaml    (4) agent secrets (tokens, auth keys)
    ↓
Resolved and complete AgentConfig
```

**Secrets rule:** if an agent does not define `llm.api_key`, it inherits the one from global.
A missing secret at a lower level never nullifies the one from a higher level.

**Startup with missing secrets:** if `agents/{id}.secrets.yaml` does not exist,
the system starts with a WARNING. Channels that require secrets will not start.
The CLI always works.

---

## Configuration files

| File | Committable | Purpose |
|------|-------------|---------|
| `config/global.yaml` | ✅ yes | System base config (LLM provider, embeddings, memory, paths) |
| `config/global.secrets.yaml` | ❌ no | Credentials registry (`providers.<name>.api_key`) |
| `config/global.secrets.yaml.example` | ✅ yes | Reference of what secrets exist |
| `config/tool_config.yaml` | ❌ no | Tool Config Protocol store (daemon-owned; `enc:` secrets inside). Not part of the 4-layer merge |
| `config/agents/{id}.yaml` | ✅ yes | Agent config: id, name, description, system_prompt, overrides, channels |
| `config/agents/{id}.secrets.yaml` | ❌ no | Agent secrets: tokens, auth_key |
| `config/agents/{id}.secrets.yaml.example` | ✅ yes | Reference of agent secrets |
| `config/global.example.yaml` | ✅ yes | Canonical reference with all documented parameters |

`.gitignore` includes: `config/*.secrets.yaml` and `config/agents/*.secrets.yaml`

---

## Instance home — `--home` / `INAKI_HOME`

By default everything lives under **`~/.inaki/`**. A single knob relocates the *entire*
instance — config, data (DBs), `secret.key`, `tool_config.yaml`, `users/` and the
knowledge index — to a different root:

```bash
inaki --home /srv/inaki-deptB daemon      # flag
INAKI_HOME=/srv/inaki-deptB inaki daemon  # env var (systemd: Environment=INAKI_HOME=...)
```

Resolution order (`infrastructure/home.py`): `--home` flag → `INAKI_HOME` env → default
`~/.inaki`. With `--home /foo`, paths re-anchor to `/foo/config`, `/foo/data/*.db`,
`/foo/knowledge/`, `/foo/users/`, `/foo/secret.key`, `/foo/config/tool_config.yaml`.

**This is the isolation boundary for harness-global resources.** `knowledge`, `scheduler`
and `faces`/`photos` are single per-process singletons (see "Tiers de recursos" in
`CLAUDE.md`) — to isolate one, run a **second harness process with its own `--home`**.

**Ports are NOT derived from the home.** A second instance must set its own `admin.port`
and `broadcast.port` (if used) in its YAML to avoid colliding with the first.

Backward-compat: the default `~/.inaki` is unchanged, so existing single-instance
deployments need no migration. The old `--config DIR` flag was replaced by `--home`
(clean break, no alias).

---

## `config/global.yaml` — all fields

```yaml
app:
  name: "Inaki"           # System name
  log_level: "INFO"       # DEBUG | INFO | WARNING | ERROR
  default_agent: "general" # Agent used by CLI without --agent

# Top-level registry of external providers. Centralizes api_key + base_url
# per vendor. Features (llm, embedding, transcription, memory.llm) only
# reference by name — they do NOT carry their own api_key/base_url.
providers:
  openrouter:
    # type: openrouter      # optional — default = the key ("openrouter")
    api_key: "sk-or-..."    # → global.secrets.yaml
    base_url: "https://openrouter.ai/api/v1"
  openai:
    api_key: "sk-..."
  groq:
    api_key: "gsk_..."
    base_url: "https://api.groq.com/openai/v1"
  ollama:
    # type: ollama — LOCAL provider, does not require api_key.
    # The entire entry is optional; if missing, the adapter default is used.
    base_url: "http://localhost:11434"
  # Multi-instance: two accounts from the same vendor (e.g. mixed billing)
  # groq-work:
  #   type: groq            # points to the "groq" adapter
  #   api_key: "gsk_work_..."

llm:
  provider: "openrouter"  # references providers.openrouter
  model: "anthropic/claude-3-5-haiku"
  temperature: 0.7
  max_tokens: 2048
  request_delay_seconds: 2.0  # Throttle: wait (seconds) before each provider call
                              # inside the agentic loop, EXCEPT the first of the turn.
                              # Prevents saturating the provider rate limiter when the
                              # model chains several tool calls (each loop iteration is
                              # one llm.complete()). The first call is never delayed.
                              # 0 disables it. Negative → 0; unparseable → default 2.0.

embedding:
  provider: "e5_onnx"     # e5_onnx (local ONNX, does not require api_key) | openai
  model_dirname: "models/e5-small"  # Dir with model.onnx + tokenizer.json (relative to ~/.inaki/)
  dimension: 384          # Vector dimension (384 for e5-small)

memory:
  db_filename: "data/inaki.db"  # SQLite file with sqlite-vec (relative to ~/.inaki/)
                                 # GLOBAL memory — shared across all agents
  default_top_k: 5               # Number of memories retrieved by vector search
  digest_size: 14                # Number of memories dumped to the digest markdown
  digest_filename: "mem/digest_{channel}_{chat_id}.md"
                                 # Digest template read by the prompt builder
                                 # (relative to ~/.inaki/). The `{channel}` and
                                 # `{chat_id}` placeholders are substituted
                                 # (sanitized) per scope: each conversation has
                                 # its own isolated digest.
                                 # Examples: `mem/digest_telegram_-1001234.md`,
                                 # `mem/digest_cli_default.md`.
  min_relevance_score: 0.5       # Minimum threshold (0.0-1.0) to persist a fact extracted
                                 # by the LLM. Filters BEFORE embedding (saves tokens).
  schedule: "0 3 * * *"          # Global cron: when the nightly consolidation runs.
                                 # A single task that iterates ALL enabled agents.
                                 # Reconciled on daemon startup: if changed here, the
                                 # row in scheduler.db is updated automatically.
  delay_seconds: 2               # Pause (seconds) between LLM extractor calls.
                                 # Applies BOTH between agents (global consolidation) and
                                 # between scopes (channel, chat_id) WITHIN each agent.
                                 # Prevents rate-limits from the remote LLM provider.
  keep_last_messages: 0          # Messages per agent to preserve after consolidation.
                                 # After extracting memories to vector storage, the
                                 # rest of the history is truncated but the last N
                                 # messages ARE PRESERVED as immediate context for the
                                 # next turn. Sentinel: 0 → use system fallback (84).
                                 # Any value > 0 is respected as-is.

tools:
  semantic_routing_min_tools: 10  # Minimum registered tools to activate semantic routing
  semantic_routing_top_k: 5       # Max number of tools selected by routing
  semantic_routing_min_score: 0.0 # Minimum cosine similarity score (0.0-1.0)
                                  # to include a tool. 0.0 = no filter.
  tool_call_max_iterations: 5     # Max tool-loop iterations per turn
  circuit_breaker_threshold: 2    # Consecutive failures before cutting the loop
  # allowed: [read_file]          # Opt-in (default: absent/null = no restriction). ONLY
                                  # affects the `delegate` flow (ephemeral one-shot sub-agent):
                                  # list of tool names the sub may use. It RESTRICTS the subset
                                  # of the toolkit INHERITED from the caller — absent = the sub
                                  # inherits the FULL caller toolkit; a list = exactly that
                                  # subset. Declared by the SUB's own definition; the caller
                                  # NEVER overrides it. Inert in a normal turn (semantic routing).
                                  # See inaki_spec.md → Delegation.

skills:
  semantic_routing_min_skills: 10  # Minimum loaded skills to activate routing
  semantic_routing_top_k: 3        # Max number of skills selected by routing
  semantic_routing_min_score: 0.0  # Minimum cosine similarity score (0.0-1.0)
                                   # to include a skill. 0.0 = no filter.

chat_history:
  db_filename: "data/history.db"  # SQLite history file (relative to ~/.inaki/)
                                 # separate from inaki.db (which uses sqlite-vec)
  max_messages: 21               # Last N messages injected into the LLM (0 = no limit)

scheduler:
  enabled: true                  # Starts the SchedulerService in daemon mode
  db_filename: "data/scheduler.db"  # SQLite scheduled tasks file (relative to ~/.inaki/)
  max_retries: 3
  retry_backoff_seconds: 10.0    # Linear wait between retries (10s, 20s, 30s...)
  max_tasks_per_agent: 20        # Active (pending/running) tasks an agent may own
  output_truncation_size: 65536
  channel_fallback:              # Resolution cascade for channel dispatch (see below)
    default: null                # str|null — default sink if no override or native match
    overrides: {}                # dict[channel_type, target] — override per source channel

workspace:
  path: "~/inaki-workspace"      # Root directory allowed for file tools (global default)
  containment: "strict"          # strict | warn | off
                                 # strict → blocks paths outside the workspace (recommended)
                                 # warn   → allows but logs a WARNING
                                 # off    → no restrictions
                                 # Affects read_file, write_file, patch_file, edit_file.
                                 # shell_exec is NOT subject to this config.
                                 # Overridable per agent in agents/{id}.yaml.

admin:
  host: "127.0.0.1"             # Admin server listen interface (loopback = most secure)
  port: 6497                    # Admin server port
  chat_timeout: 300.0           # Timeout (seconds) to wait for agent response
                                # in POST /admin/chat/turn. Increase for slow models.
  # auth_key → in global.secrets.yaml
```

### Admin server — exposed endpoints

The admin server exposes the following endpoints under `http://{admin.host}:{admin.port}/`:

All endpoints except `/health` require the `X-Admin-Key` header. This is the
**only** HTTP surface of the daemon — routing is by `agent_id`, there is no
per-agent REST server.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health ping (no auth) |
| POST | `/inspect` | Inspect the prompt pipeline for an agent |
| POST | `/consolidate` | Consolidate memory — body `{"agent_id": "X"}` for one agent, empty for all |
| POST | `/scheduler/reload` | Reload scheduler |
| POST | `/scheduler/run` | Fire a task now — body `{"task_id": N}`. Non-destructive test run: dispatches the trigger once without touching `status`/`next_run`/`executions_remaining`. `404` if the task does not exist. Client: `inaki scheduler run <ID>` |
| POST | `/admin/reload` | Hot-reload the daemon (closes channels, reloads config, restarts) |
| GET | `/admin/agents` | List registered agent ids |
| GET | `/admin/agent/info` | Agent metadata (`?agent_id=X` → id, name, description) |
| POST | `/admin/chat/turn` | Send a chat turn to an agent |
| POST | `/admin/chat/task` | Oneshot ephemeral task (loads history, does not persist) |
| GET | `/admin/chat/history` | Get agent history |
| DELETE | `/admin/chat/history` | Clear agent history |
| GET | `/admin/tool/list` | List the tools registered in an agent |
| POST | `/admin/tool/invoke` | Invoke a tool directly |
| POST | `/admin/send` | Send text/media to a channel from an agent |

#### POST `/admin/chat/turn`

```json
// Request body
{
  "agent_id": "dev",
  "session_id": "uuid-del-cliente-cli",
  "message": "Hola, ¿cómo estás?",
  "channel": "telegram",      // optional — declares the turn's channel_type
  "chat_id": "-1001234"       // optional — both-or-none with channel
}

// Response 200
{
  "reply": "Estoy bien, ¿en qué te ayudo?",
  "agent_id": "dev",
  "session_id": "uuid-del-cliente-cli"
}
```

`channel` + `chat_id` are optional and must be sent together (both-or-none,
`422` otherwise). When present, the `ChannelContext` uses that `channel_type`
and the turn operates on the real history scope `(agent_id, channel, chat_id)`
— useful to simulate a turn as if it came from another channel. When omitted,
`channel_type="cli"` and the legacy shared scope `("", "")` are used.

Possible errors: `401` (missing X-Admin-Key), `404` (agent_id not registered), `422` (invalid body), `500` (internal agent error).

#### POST `/consolidate`

With `{"agent_id": "dev"}` consolidates only that agent (`404` unknown agent,
`503` if the agent has `memory.enabled=false`). With an empty body (or no
`agent_id`) consolidates all agents.

#### GET `/admin/chat/history?agent_id=dev`

```json
// Response 200
{
  "agent_id": "dev",
  "messages": [
    {"role": "user", "content": "Hola", "timestamp": "2026-01-01T12:00:00"},
    {"role": "assistant", "content": "¡Hola!", "timestamp": "2026-01-01T12:00:01"}
  ]
}
```

#### DELETE `/admin/chat/history?agent_id=dev`

Returns `204 No Content`. Deletes the active history of the agent (affects all channels — CLI, Telegram, etc.).

---

## `knowledge:` — External knowledge sources

The `knowledge:` section lives **only in `global.yaml`** — it cannot be configured per agent.
It controls the external knowledge retrieval pipeline (RAG) that runs before each turn.

```yaml
knowledge:
  enabled: true                    # If false, the pre-fetch is skipped entirely.
                                   # Default: true.

  include_memory: true             # If true, the agent's SQLite memory is automatically
                                   # registered as a "memory" source.
                                   # Default: true.

  top_k_per_source: 3              # Max results per source (global default).

  min_score: 0.5                   # Minimum cosine score to include a chunk.
                                   # Range: 0.0-1.0. Default: 0.5.

  max_total_chunks: 10             # Total chunk cap after fan-out to all sources
                                   # (sorted by score desc, then truncated).

  token_budget_warn_threshold: 4000
                                   # If the estimated total tokens
                                   # (chunks + digest + skills) exceeds this value,
                                   # a WARNING is emitted with the breakdown.
                                   # Heuristic: len(text) / 4.
                                   # 0 = warning disabled.

  sources:
    - id: docs-proyecto            # Unique source ID (used in CLI and DB paths)
      type: document               # "document" = folder of files
      enabled: true                # If false, the source is ignored on startup.
      description: "Project docs"  # Description injected into the system prompt
      path: ~/proyecto/docs/       # Folder to index (supports ~). Required.
      glob: "**/*.md"              # Glob pattern to select files.
                                   # Examples: "**/*.md", "**/*.{md,txt,pdf}"
      chunk_size: 500              # Size of each chunk in words.
      chunk_overlap: 80            # Overlap between consecutive chunks (in words).
      top_k: 3                     # Max results from this source.
      min_score: 0.5               # Minimum score for this source (overrides global).

    - id: mi-base                  # Unique source ID
      type: sqlite                 # "sqlite" = user-built pre-existing DB
      enabled: true
      description: "My knowledge base"
      path: ~/data/knowledge.db    # Path to the user's SQLite DB. Required.
      top_k: 3
      min_score: 0.5
```

#### Source `type: sqlite` — User-built pre-existing database

Allows connecting a SQLite database that the user built and manages on their own.
Inaki **does not index or write to** this DB — it only queries it for vector searches.

**Required schema:**

```sql
-- Text and metadata table (id must be the integer PRIMARY KEY)
CREATE TABLE chunks (
    id            INTEGER PRIMARY KEY,
    source_path   TEXT NOT NULL,
    content       TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}'
);

-- vec0 virtual table with 384-dimension embeddings (e5-small)
-- The rowid of chunk_embeddings must match chunks.id
CREATE VIRTUAL TABLE chunk_embeddings USING vec0(embedding FLOAT[384]);
```

**Important notes:**

- The dimension **must be exactly 384** — this is the dimension of the e5-small model used internally by Inaki. If the DB uses a different dimension, the source is skipped on startup with a clear error in the logs.
- `chunk_embeddings.rowid` is used for the JOIN with `chunks.id` — they must match.
- `metadata_json` is optional but must be valid JSON if present (or `NULL`/`'{}'`).
- Inaki validates the schema on the first search. If validation fails, the source is disabled for that session and an `ERROR` is logged with the source name and the exact reason.

**Minimal insertion example:**

```python
import sqlite3, struct, numpy as np

conn = sqlite3.connect("knowledge.db")
conn.enable_load_extension(True)
conn.load_extension("vec0")  # sqlite-vec

conn.execute("""
    CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY, source_path TEXT NOT NULL,
        content TEXT NOT NULL, metadata_json TEXT DEFAULT '{}'
    )
""")
conn.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(embedding FLOAT[384])
""")

content = "Texto del chunk a indexar"
embedding = np.random.randn(384).astype(np.float32)  # replace with your real embedder
vec_bytes = struct.pack("384f", *embedding)

conn.execute("INSERT INTO chunks (source_path, content) VALUES (?, ?)", ("/ruta/doc.md", content))
row_id = conn.lastrowid
conn.execute("INSERT INTO chunk_embeddings (rowid, embedding) VALUES (?, ?)", (row_id, vec_bytes))
conn.commit()
```

### Document indexing

Documents are indexed offline with the CLI command:

```bash
inaki knowledge index docs-proyecto   # Index or re-index the source
inaki knowledge list                   # List configured sources
inaki knowledge stats docs-proyecto    # Index statistics
```

Indexing is **incremental**: only files whose `mtime` changed since the last
indexing are re-processed. Embeddings are persisted in `~/.inaki/knowledge/{id}.db`.

### Supported formats

| Format | Chunking strategy |
|--------|-------------------|
| `.md`  | Split by headers (`#`/`##`/`###`), sliding window within each section |
| `.txt` | Pure sliding window |
| `.pdf` | Page-by-page extraction with `pypdf`, sliding window over the total text |
| other  | Pure sliding window (plain text) |

### Index DB schema (`~/.inaki/knowledge/{id}.db`)

```sql
CREATE TABLE chunks (
    id          TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    file_mtime  REAL NOT NULL,
    chunk_idx   INTEGER NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE VIRTUAL TABLE chunk_embeddings USING vec0(
    id        TEXT PRIMARY KEY,
    embedding FLOAT[384]
);
CREATE TABLE files_indexed (
    file_path   TEXT PRIMARY KEY,
    mtime       REAL NOT NULL,
    chunk_count INTEGER NOT NULL
);
```

---

## `config/global.secrets.yaml`

The credentials registry lives under `providers:` and is merged with the `providers:`
from `global.yaml` (deep-merge field by field).

```yaml
providers:
  openrouter:
    api_key: "sk-or-..."
  openai:
    api_key: "sk-..."
  groq:
    api_key: "gsk_..."
```

An entry declared in `global.yaml` (e.g. with `base_url`) is completed with
the `api_key` from this file — there is no need to repeat fields.

---

## `config/tool_config.yaml` — Tool Config Protocol

Credentials and settings for tools (builtin and `ext/`) live in their **own file**,
`config/tool_config.yaml`, under a `tool_config:` root with one namespace per tool.
This file is **owned by the daemon**: the store reads it at startup and rewrites it
on `configure`. It is kept **separate from `global.secrets.yaml`** — that one is
yours, hand-authored (`providers.*`, tokens), and the daemon never touches it. It
does **not** participate in the 4-layer merge. **The user can configure tools from
any channel by just talking to the agent** — the tool exposes `operation=configure`
and persists here via the protocol (`IToolConfigStore`); hand-editing the YAML works too.

```yaml
tool_config:
  web_search:
    api_key: "enc:gAAAA..."     # cifrada en reposo (Fernet, clave en ~/.inaki/secret.key)
    search_depth: basic         # los campos no sensibles quedan en plano
    max_results: 5
  exchange:
    username: "dominio\\alberto"
    password: "enc:gAAAA..."
    mail: alberto@empresa.com
    ews_url: https://mail.empresa.com/EWS/Exchange.asmx
    timezone: Europe/Madrid
```

How it works: a tool declares `config_namespace` (class attr) and the container
injects the store at construction. `configure` writes take effect immediately
(no restart) and survive restarts. Sensitive fields are encrypted at rest with
the auto-generated key in `~/.inaki/secret.key` (0600) and shown masked by
`show_config`. Threat model honesty: key and data share the disk — encryption
protects against accidental YAML disclosure, not against an attacker with
filesystem access. **Back up `secret.key`**: if it's lost, encrypted values are
unrecoverable (the tool will simply ask you to configure again).

Without credentials the tool still registers; the LLM receives a
`CONFIGURATION REQUIRED` error instructing it to ask the user and call
`configure` — that's the protocol's conversational UX.

Current consumers: `web_search` (builtin), `exchange_calendar`/`exchange_mail`,
`fal_music`, `replicate_music` (ext).

**Migration from previous versions:** earlier builds stored `tool_config:` inside
`global.secrets.yaml`. On first startup the daemon moves that block to
`config/tool_config.yaml` automatically (`migrate_tool_config_to_own_file`),
preserving the rest of `global.secrets.yaml`. `secret.key` is unchanged, so
encrypted (`enc:`) values keep decrypting — no reconfiguration needed.

---

## `config/agents/{id}.yaml` — complete structure

```yaml
id: "general"                    # Unique agent identifier (= filename)
name: "Inaki-g"                  # Display name
description: "Asistente general" # Short description
system_prompt: |                 # Agent base prompt (required)
  Eres Inaki, un asistente personal inteligente.
  Eres conciso, directo y útil.

# LLM overrides — only the fields that change, the rest is inherited from global
llm:
  model: "anthropic/claude-3-5-haiku"
  # provider, base_url, temperature, max_tokens → inherited from global

# Embedding overrides (optional)
# embedding:
#   provider: "e5_onnx"

# Memory — flags valid ONLY per-agent.
# The rest of memory.* is defined in global.yaml and MUST NOT be overridden here.
memory:
  enabled: true        # If false, this agent does NOT participate in the global
                       # nightly consolidation. Default: true.
                       # The command `inaki consolidate --agent {id}` ignores
                       # this flag and consolidates the specified agent anyway.

  # Memory reconciliation (optional — default: disabled)
  reconcile_enabled: false            # Enables the reconciliation feature for this agent.
                                      # When true, a builtin task `reconcile_memory_{id}`
                                      # is created in scheduler.db automatically.
  reconcile_schedule: "0 4 * * 1"    # Cron for the builtin reconcile task.
                                      # Default: lunes 4am (user timezone). Reconciled on
                                      # daemon startup: if changed, the DB row is updated.
  reconcile_similarity_threshold: 0.80  # Minimum cosine similarity (0.0–1.0) for two
                                         # memories to be considered neighbors and grouped
                                         # into a cluster for LLM evaluation. Default: 0.80.
  reconcile_top_k: 10                # Max neighbors retrieved per seed via `search_with_scores`.
                                     # Must be generous enough to compensate for the lack of
                                     # native scope filtering in the vector search. Default: 10.
  reconcile_llm:                     # Optional dedicated sub-agent for reconciliation.
    agent_id: memory_reconciler      # Must be a configured agent (see example YAML at
                                     # config/agents/sub-agents/memory_reconciler.example.yaml).
                                     # If absent, the agent's own LLM is used with a
                                     # hardcoded prompt.

# Workspace — path containment for file tools (read_file, write_file, patch_file, edit_file)
# shell_exec is NOT affected by this config.
workspace:
  path: "/Users/alberto/tmp/mi_workspace"  # Allowed root directory (default: process cwd)
  containment: "strict"                    # strict | warn | off (default: strict)

# Channels available for this agent
# Sensitive values (tokens, auth_key) go in {id}.secrets.yaml
channels:
  telegram:
    allowed_user_ids: ["123456789"]  # Empty list = all allowed
    reactions: true                  # React with emojis to messages
    debug: false
    voice_enabled: true              # Accepts voice/audio/video_note (default: true)
                                     # Requires resolved [transcription] block
    add_llm_timestamp: false         # Prepends "[YYYY-MM-DD HH:MM:SS TZ] " to the
                                     # content of each USER/ASSISTANT message
                                     # when building the prompt for the LLM
                                     # (private chats + groups). Default: false.
  # `cli` admite un campo opcional `user:` que enlaza un perfil per-user del
  # directorio `~/.inaki/users/{channel}/`. Ver la sección "Per-user context
  # files" más abajo para el detalle.
  cli:
    user: "alberto"                  # Carga ~/.inaki/users/cli/alberto.md
```

> La superficie REST vive íntegramente en el **admin server** (un solo puerto
> global, ruteo por `agent_id`, auth `X-Admin-Key`). Ya no existe el canal
> `channels.rest` per-agente.

---

## Per-user context files — `~/.inaki/users/`

Cada turno del agente inyecta un bloque opcional de "contexto del remitente" en
el system prompt. Antes existía un único `~/.inaki/USER.md` global; ahora se
resuelve por canal y por usuario:

```
~/.inaki/users/{channel_type}/_common.md     ← común al canal (se inyecta ANTES)
~/.inaki/users/{channel_type}/{username}.md  ← per-user, preferente
~/.inaki/users/{channel_type}/{user_id}.md   ← per-user, fallback
(nada)                                       ← si ninguno existe
```

- **`_common.md` — contexto común al canal**: si existe, su contenido se
  concatena **antes** del archivo per-user. Pensado para reglas que aplican a
  todos los usuarios del canal, p. ej. formato de respuesta ("no uses tablas
  markdown en Telegram, no las renderiza"). El prefijo `_` evita colisión con un
  `{username}.md` que se llamara `common`. Se inyecta aunque no haya archivo
  per-user: el contexto común del canal solo también vale.
- **Scope por canal**: `alberto` en Telegram ≠ `alberto` en CLI. Cada canal
  tiene su propio subdirectorio. `_common.md` también es por canal — el de CLI
  no se hereda en Telegram.
- **Preferencia por `username`**: el handle humano (sin `@`) es más legible y
  estable que un ID numérico. Cuando el usuario no tiene username configurado
  (Telegram lo permite), cae al `user_id`.
- **Sin archivo → contexto vacío**. No hay fallback global ni warning. Pensado
  para uso doméstico: si un usuario no tiene archivo, el agente lo trata sin
  contexto previo.

### Cómo cada canal resuelve `channel_type` y los identificadores

| Canal | `channel_type` | `username` | `user_id` |
|-------|----------------|------------|-----------|
| Telegram (privado) | `telegram` | `@username` del remitente (sin `@`) | `from_user.id` |
| Telegram (grupo)   | `telegram` | siempre `None` — la identidad va en el contenido | `agent_id` del flush |
| CLI (admin chat)   | `cli`      | `channels.cli.user` del YAML del agente (opcional) | `session_id` del cliente |
| REST `/chat/turn`  | `cli`      | igual que CLI                                       | `session_id` |

### Ejemplo de archivo

`~/.inaki/users/telegram/alberto.md`:

```markdown
Hablás con Alberto, el operador del sistema.
- Stack preferido: Python + hexagonal architecture.
- Idioma: español rioplatense (voseo).
- Evitá disclaimers innecesarios.
```

### Auto-creación de subdirectorios

El daemon, al arrancar, crea `~/.inaki/users/{channel}/` por cada canal
configurado en cualquier agente (`AgentConfig.channels.keys()`). No hace falta
hacer `mkdir` manual — basta entrar a `~/.inaki/users/` y aparecen los
subdirectorios listos para depositar archivos per-user.

### Migración desde `USER.md`

El path legacy `~/.inaki/USER.md` ya **no se lee**. Migrar:

```bash
mv ~/.inaki/USER.md ~/.inaki/users/telegram/{tu_username}.md
```

Si querés el mismo contexto en CLI/REST, copiá el archivo a
`~/.inaki/users/cli/{tu_user}.md` y configurá `channels.cli.user: {tu_user}`
en el YAML del agente.

---

## `workspace` — path containment for file tools

Each agent can declare a `workspace` to control which paths the file tools
can access. It is configured in `config/agents/{id}.yaml`:

```yaml
workspace:
  path: "/Users/alberto/tmp/mi_workspace"  # Allowed root directory
  containment: "strict"                    # strict | warn | off
```

**Containment modes:**

| Mode | Behavior |
|------|----------|
| `strict` | Blocks any path outside `workspace.path`. The tool returns an error to the LLM. **Default.** |
| `warn` | Allows paths outside the workspace but logs a WARNING. Useful for debugging. |
| `off` | No restrictions. The tool accesses any path on the system. |

**Tools affected by `workspace.containment`:**

| Tool | Sandboxed? |
|------|------------|
| `read_file` | ✅ yes |
| `write_file` | ✅ yes |
| `patch_file` | ✅ yes |
| `edit_file` | ✅ yes |
| `shell_exec` | ❌ no — executes commands without path restrictions |
| `delegate`, `scheduler`, rest of builtins | ❌ not applicable |

> **Note:** `shell_exec` is an extension in `ext/` and has no containment of any kind.
> If the LLM can call `shell_exec`, it can operate on any path on the system.

If `workspace.path` is not defined in the agent config, the process working directory
at startup is used. To avoid ambiguities in production (systemd),
always specify an absolute path.

---

## `transcription` — voice transcription (Telegram)

Enables transcription of voice, audio, and video_note messages in Telegram.
Defined in `config/global.yaml` (or overridable per-agent) and activated with
`channels.telegram.voice_enabled: true` (default).

```yaml
transcription:
  provider: "groq"                     # references providers.groq
  model: "whisper-large-v3-turbo"
  language: "es"                        # ISO-639-1; null = autodetect
  timeout_seconds: 60
  max_audio_mb: 25                      # Groq limit; larger audio files are rejected without calling the provider
```

Credentials (`api_key`, `base_url`) do NOT go in this block — they are resolved
from `providers.groq` in the registry.

**Feature flag on the agent:**

```yaml
channels:
  telegram:
    voice_enabled: true   # default — accepts voice/audio/video_note
    # voice_enabled: false — silent drop, the bot ignores audio files
```

**Voice handler flow:**

1. Authorized user (`allowed_user_ids`) — otherwise, silent drop.
2. `voice_enabled: true` — otherwise, silent drop.
3. Size ≤ `max_audio_mb` — otherwise, ❌ reaction + reply with the size.
4. 👂 reaction at start.
5. Transcription → same pipeline as a text message (HTML reply + ✅/❌).

**Common startup errors:**

- Agent with `voice_enabled: true` and no resolved `transcription:` block
  → fails during bootstrap with a clear error asking to add `transcription:`
  or set `voice_enabled: false`.
- `providers.<provider>.api_key` missing for the provider referenced by
  `transcription.provider` → `ConfigError` at startup (fail-fast, before
  instantiating adapters).

> ⚠ **Privacy:** the audio is sent to the external provider (currently: Groq). For
> sensitive content set `voice_enabled: false` on that agent or wait for a
> local provider to become available. The app does NOT persist the audio; the transcribed
> text does remain in chat_history and can feed into memory.

---

## `broadcast` — broadcast channel between Inaki instances

Allows two or more Inaki instances (e.g. one on each Raspberry Pi) to
share the conversational context of a Telegram group in real time.
One instance acts as the **server** (listens for connections) and the rest as
**clients** (connect to the server). Star topology: one server, N clients.

### Config blocks

**`allowed_chat_ids`** — authorized groups (added to the existing channel config):

```yaml
channels:
  telegram:
    api_key: "..."
    allowed_user_ids: [12345]
    allowed_chat_ids: [-1001234567890]  # list of allowed groups; negative integers
```

If `allowed_chat_ids` is empty or absent, only private chats from users in
`allowed_user_ids` are admitted. To enable groups, their chat_ids must be explicitly listed.

---

**`channels.telegram.broadcast`** — server mode (this instance listens for incoming connections):

```yaml
channels:
  telegram:
    api_key: "..."
    broadcast:
      port: 1234                          # TCP listen port (1024..65535)
      auth: "shared-secret-entre-agentes" # HMAC-SHA256 shared secret
      bot_username: "inaki_a_bot"         # bot username without @, for mention detection
      behavior: mention                   # listen | mention | autonomous
      rate_limiter: 5                     # max proactive responses per window per chat
      rate_limiter_window: 30             # window duration in seconds (default 30)
```

---

**`channels.telegram.broadcast`** — client mode (this instance connects to the server):

```yaml
channels:
  telegram:
    api_key: "..."
    broadcast:
      remote:
        host: "192.168.1.10:1234"           # server ip:port
        auth: "shared-secret-entre-agentes" # must match the server
      bot_username: "inaki_b_bot"
      behavior: autonomous
      rate_limiter: 5
      rate_limiter_window: 300            # ⚠ recommended 300s (5min) for autonomous
```

---

### `broadcast.emit` — what event types each bot emits

Each bot has flags per `event_type` that control **what** it emits to the channel. Defaults
designed to maintain backward-compat and avoid accidental duplicates:

```yaml
channels:
  telegram:
    broadcast:
      port: 1234
      auth: "..."
      emit:
        assistant_response: true   # default true — LLM responses after group turn
        user_input_voice: false    # default false — audio transcriptions
        user_input_photo: false    # default false — processed photo descriptions
```

**When to enable `user_input_voice` / `user_input_photo`:**

These events are useful when there are **multiple bots in the same Telegram group** and only
some have the corresponding capabilities (audio transcription, visual recognition).
Enabling them allows the bot with the capability to **share the processed result**
so that the other bots have that context in their buffers.

**Configuration rule**: enable each flag on **a single bot** in the group — the one
that owns the capability. If two bots emit the same event, the receiver will see it twice
(there is no deduplication; it's an admin decision).

`assistant_response` stays `true` by default to maintain the behavior of the
existing broadcast (bots see the responses from other bots).

---

**`memory.channels_infused`** — limit which channels feed into memory consolidation:

```yaml
memory:
  channels_infused: ["telegram"]  # null or absent = all channels are consolidated
```

Useful when you have an agent active on both CLI and Telegram but only want
Telegram conversations to enter long-term memory.

---

### Behavior modes (`behavior`)

| Mode | Description |
|------|-------------|
| `listen` | The bot never responds. It only absorbs context in the broadcast buffer. Useful for an "observer" agent. |
| `mention` | The bot responds only when someone mentions it with `@bot_username`. **Default in groups.** |
| `autonomous` | The LLM decides whether to respond. If it has nothing useful to contribute, it responds with `[SKIP]` internally and the system sends nothing to the group. Additionally, the bot triggers its pipeline on **any broadcast message** (bot-to-bot): the user_input is injected with a `[<source>]` prefix and the LLM decides whether to respond or emit `[SKIP]`. Allows two bots to converse with each other in a group. |

The **rate limiter** (`rate_limiter: 5`) applies in `autonomous` mode for both paths:
incoming Telegram messages **and** bot-to-bot broadcast triggers. It allows exactly
N messages per fixed window (configurable via `rate_limiter_window`, default `30` seconds),
per `(agent, chat_id)` combination. The N+1th emission within the same window is discarded
until the window resets.

> ⚠ **Warning for `behavior: autonomous`**: a full bot-to-bot cycle (flush delay
> of 7-21s + LLM + network) typically takes between 15 and 40 seconds. If `rate_limiter_window` is
> shorter than the cycle, the counter resets between exchanges and the limiter is **ineffective**:
> bots can talk indefinitely. For groups with multiple autonomous bots, configure
> at least `rate_limiter_window: 300` (5 minutes).

#### Runtime override — `/ratelimit`

To tune the rate limiter without restarting the daemon, any user in `allowed_user_ids`
can use the `/ratelimit` command:

```text
/ratelimit                  → shows current count and window
/ratelimit <count>          → changes the count (clamped 1..99)
/ratelimit <count> <window> → changes both (count 1..99, window 1..900s)
/ratelimit reset            → reverts to config values
```

The change applies to the entire bot (all chats it participates in) and persists **only in
memory** — on daemon restart, values are read again from
`~/.inaki/config/agents/{id}.yaml`. Useful for quickly stopping a runaway bot-to-bot loop
or temporarily raising the limit during an active conversation.

---

### Getting a group's `chat_id` — bootstrap with `/chatid`

To authorize a group in `allowed_chat_ids` you need to know its numeric `chat_id`.
Telegram's interfaces do not show it. The bootstrap flow is:

1. Add the bot to the group as an administrator.
2. From your account (which is already in `allowed_user_ids`), send the message `/chatid` in
   the group.
3. The bot replies with the group's numeric `chat_id` (e.g. `-1001234567890`).
4. Copy that number into `allowed_chat_ids` in the agent config.
5. Restart the daemon: `systemctl restart inaki`.

**Why doesn't `/chatid` require `allowed_chat_ids`?** Precisely to solve the
chicken-and-egg problem: the group can't be in the whitelist if you don't know its id yet.
That's why the command bypasses `allowed_chat_ids` validation.

The command **does respect `allowed_user_ids`** — only authorized users can query it.
An attacker who manages to add the bot to an unknown group cannot do anything useful
with just the chat_id.

---

### NTP requirement — clock synchronization

The broadcast channel uses **HMAC-SHA256** with a freshness window of **60 seconds**.
When validating an incoming message, the receiver computes `|now − message_timestamp| > 60s` and if
true, silently discards it.

**Both Raspberry Pis (or any pair of agents) must have their clocks synchronized
via NTP.** The default NTP client in Raspberry Pi OS (`systemd-timesyncd` or `chrony`)
is sufficient. No additional configuration is needed if the Pi has internet access.

**Failure mode:** if the clocks drift more than ~60 seconds apart, **all
broadcast messages are discarded** without any visible warning to the user. The only
indication is log entries with the event `broadcast.message.dropped.stale_timestamp`.
This condition is operationally invisible if logs are not monitored, which is why this
requirement is critical.

To verify that NTP is active:
```bash
timedatectl status          # see "NTP service: active"
systemctl status systemd-timesyncd  # or chrony
```

---

## `config/agents/{id}.secrets.yaml`

```yaml
channels:
  telegram:
    token: "7xxxxxxx:AAF..."     # Bot token from BotFather

# providers not defined here → inherits from global + global.secrets.
# If the agent needs a different api_key (e.g. another Groq account):
# providers:
#   groq:
#     api_key: "gsk_agent_specific_..."
```

---

## Field merge rules

| Field | Behavior |
|-------|----------|
| `llm` (block) | Field-by-field merge. Absent fields are inherited. No `api_key`/`base_url` (they live in `providers`). |
| `providers` (block) | Field-by-field merge by key. A lower layer can complete an entry declared above. |
| `providers.<name>.api_key` | Only in `*.secrets.yaml`. An agent can redefine an entire provider. |
| `embedding` | Field-by-field merge if defined. No `api_key`/`base_url`. |
| `transcription` (block) | Field-by-field merge. No `api_key`/`base_url` (they live in `providers`). |
| `channels.telegram.voice_enabled` | Per-agent. Default `true`. If `true`, requires a `transcription:` block. |
| `memory.db_filename` / `digest_filename` / `default_top_k` / `min_relevance_score` / `schedule` / `delay_seconds` / `keep_last_messages` | **Only in `global.yaml`**. An agent cannot override them (semantically it makes no sense: the memory is globally shared). |
| `memory.enabled` | **Only per-agent in `agents/{id}.yaml`**. Default `true`. Filters which agents participate in the global nightly consolidation. |
| `memory.reconcile_enabled` / `reconcile_schedule` / `reconcile_similarity_threshold` / `reconcile_top_k` / `reconcile_llm` | **Only per-agent in `agents/{id}.yaml`**. Default `false` for `reconcile_enabled`. Activating it causes a builtin task `reconcile_memory_{id}` to be auto-created in `scheduler.db`. |
| `channels` | Only in the agent. Does not exist in global. |
| `channels.*.token` / `auth_key` | Only in `*.secrets.yaml`. |
| `system_prompt` | Required on each agent. No default value. |
| `id`, `name`, `description` | Required on each agent. |

---

## Path resolution

Runtime path fields (`*_filename`, `*_dirname`) are resolved as follows:

- **Relative paths** (e.g. `"data/inaki.db"`) are anchored under `~/.inaki/`.
- **Absolute paths** (e.g. `"/srv/inaki/data/inaki.db"`) are used as-is.
- **Tildes** (`~/...`) are expanded to the user's home directory.
- The special SQLite value `:memory:` passes through without being interpreted as a path.

The root `~/.inaki/` is fixed — it is the same one used by config/agents/secrets —
following the principle of separation between user data and the project tree.

Default layout:
```
~/.inaki/
├── config/            # Global + secrets YAMLs
├── agents/            # Per-agent YAMLs + secrets
├── data/              # SQLite DBs (inaki.db, history.db, scheduler.db, embedding_cache.db)
├── models/            # ONNX models (e.g. e5-small/)
├── mem/               # Digest markdown — one file per scope (digest_{channel}_{chat_id}.md)
└── ext/               # User extensions
```

If you need to move storage to a different root (e.g. dedicated disk on Pi 5), pass
absolute paths in `~/.inaki/config/global.yaml`:
```yaml
embedding:
  model_dirname: "/srv/inaki/models/e5-small"
  cache_filename: "/srv/inaki/data/embedding_cache.db"

memory:
  db_filename: "/srv/inaki/data/inaki.db"
  digest_filename: "/srv/inaki/mem/digest_{channel}_{chat_id}.md"

chat_history:
  db_filename: "/srv/inaki/data/history.db"

scheduler:
  db_filename: "/srv/inaki/data/scheduler.db"
```

---

## Adding a new agent

1. Create `config/agents/miagente.yaml` with `id`, `name`, `description`, `system_prompt`
2. Create `config/agents/miagente.secrets.yaml` with the required tokens
3. Restart the daemon: `systemctl restart inaki`

The `AgentRegistry` automatically scans `config/agents/*.yaml` on startup.
No manual registration or code restart is needed.

---

## Memory consolidation — configuration

Long-term memory is fed from a single global scheduled task that
fires according to `memory.schedule` (cron in `global.yaml`). That task iterates all
agents with `memory.enabled = true` and calls each one in sequence with a
`memory.delay_seconds` pause between them.

### Reconciliation on daemon startup

On startup, `AppContainer.startup()` reconciles the state of the builtin
`consolidate_memory` task (id=1) with the config:

| Situation | Action |
|-----------|--------|
| The task does not exist in `scheduler.db` | Created with the config schedule and `next_run` computed via croniter. |
| The `schedule` in the DB does not match the one in config | The schedule is updated and `next_run` is recomputed. |
| The task is in `FAILED` state (leftover from old runs) | Reset to `pending`, `retry_count=0` and `next_run` is recomputed. |
| `next_run` is `NULL` | Recomputed via croniter. |

This means that **changing `memory.schedule` in `global.yaml` and restarting the
daemon is enough** to apply the new schedule. There is no need to edit `scheduler.db`
manually.

### Manual trigger

| Command | Effect |
|---------|--------|
| `inaki consolidate` | Runs the global use case — iterates all agents with `memory.enabled=true` respecting `delay_seconds`. |
| `inaki consolidate --agent dev` | Consolidates only `dev`, ignores the `enabled` flag. |

Both start `AppContainer`, run the consolidation as a one-shot, and print the
result to stdout. They do not start the scheduler or the channels.

### Relevance filter

The `ConsolidateMemoryUseCase` discards facts extracted by the LLM whose
`relevance` is below `memory.min_relevance_score`. The filter is applied
**before** generating embeddings, so discarding saves embedder calls
and storage in `inaki.db`.

### History retention after consolidation

After a successful consolidation (extraction + memory persistence OK),
the use case calls `history.mark_infused(agent_id)` + `history.trim(agent_id,
keep_last=N)` where `N` comes from `memory.keep_last_messages` with the sentinel
`0 → 84`. This means:

- The **last N messages** from the agent remain in `history.db` as immediate
  context for the next turn (the prompt builder injects them normally).
- The **rest** is deleted — the relevant facts are already in the vector
  memory (`inaki.db`) and the recent memories in the per-scope digests
  under `~/.inaki/mem/digest_{channel}_{chat_id}.md`.
- The **N preserved messages** are marked with `infused=1` so that the next
  consolidation **does not reprocess them** (avoids duplicates in vector
  memory from re-extraction).

**Transactionality:** if any step fails (LLM, parsing, embedding,
persistence, mark_infused), `trim` is NOT called. The history stays intact
and the next run retries the same content. There is no intermediate state.

**Idempotency:** running `/consolidate` twice in a row is a no-op
the second time: `load_uninfused` returns empty and the use case returns
"No new messages to consolidate." without touching anything.

### `infused` flag — gate against reprocessing

The `history` table has a column `infused INTEGER NOT NULL DEFAULT 0`:

- **`0`** — message pending extraction
- **`1`** — message already processed by the extractor in a previous run

The consolidation flow is:

1. `load_uninfused(agent_id)` — SELECT on `WHERE infused = 0`
2. If empty → no-op (return early)
3. Extraction + persistence (if any step fails, the flag is not touched)
4. `mark_infused(agent_id)` — `UPDATE SET infused = 1 WHERE infused = 0`
5. `trim(agent_id, keep_last=N)` — DELETE all except last N (the N that
   remain include the rows marked in step 4)

`load()` and `load_full()` ignore the flag — the prompt builder and `/history`
always see the full context, whether processed or not.

**Automatic migration:** DBs created before this change are migrated on the
first `_ensure_schema` via `ALTER TABLE ADD COLUMN infused INTEGER NOT NULL
DEFAULT 0` followed by `UPDATE history SET infused = 1` (it is assumed that
pre-existing rows were part of a stable state).

`/clear` (slash command) still does a full wipe — it is the manual mechanism
to discard the thread. Separate from consolidation.

The Telegram bot also exposes `/reconcile` to trigger `ReconcileMemoryUseCase`
on demand (same auth rules as `/consolidate`). Only available when
`memory.reconcile_enabled: true` for the agent.

### Dedicated LLM for consolidation — `memory.llm`

By default, the `ConsolidateMemoryUseCase` uses the same `ILLMProvider` as the
agent (`llm.*`). This is convenient, but has a concrete pitfall: if the agent's
LLM is a **reasoning model** with high `reasoning_effort` (e.g.
`openai/gpt-oss-120b` on Groq), the model consumes the entire `max_tokens`
budget reasoning internally and returns `content: ""`. The consolidation parser
fails with `ConsolidationError: "El LLM no devolvió JSON válido. Respuesta: "`
(empty) and memories are never extracted.

The `memory.llm` sub-block allows **partial override** of `llm.*` ONLY for
consolidation, without touching the conversational LLM:

```yaml
providers:
  groq:   { api_key: KEY_GROQ, base_url: https://api.groq.com/openai/v1 }
  openai: { api_key: KEY_OPENAI }

llm:                          # Base (agent chat)
  provider: groq
  model: openai/gpt-oss-120b
  reasoning_effort: high
  max_tokens: 2048

memory:
  enabled: true
  llm:                        # Override ONLY for consolidation
    provider: openai          # different vendor — creds resolved from providers.openai
    model: gpt-4o-mini
    reasoning_effort: null    # turns off reasoning
    max_tokens: 8192
    # temperature → inherited from llm.*
```

**Merge semantics (field-by-field):**

| `memory.llm.*` YAML | Behavior |
|----------------------|----------|
| Key ABSENT | Inherits the value from `llm.*`. |
| Key with concrete value (e.g. `max_tokens: 8192`) | Overrides the base. |
| Key with explicit `null` value (e.g. `reasoning_effort: null`) | Overrides the base with `None` (override, not inheritance). |

**Startup validation:** if the override points to a `provider` that does not exist
in the `providers:` registry and the corresponding adapter requires credentials,
the daemon fails at startup with `ConfigError` — not silently during the
next consolidation.

**Wiring:** `AgentContainer` compares the merged `LLMConfig` against `llm.*`;
if they are identical, it **reuses** the same provider instance (no HTTP client
duplication). If they differ, it instantiates a dedicated provider via
`LLMProviderFactory.create_from_resolved(resolved)`, where the `ResolvedLLMConfig`
composes the override with the registry credentials.

**When to use it:**

- Your chat LLM is a reasoning model and consolidation is broken → canonical
  case. Point to a non-reasoning model (`llama-3.3-70b-versatile`,
  `gpt-4o-mini`, etc.).
- You want a **cheaper** model for consolidation — it's structured extraction,
  it doesn't need the most powerful model.
- Chat uses one provider and memory uses another (e.g. chat on local Ollama,
  consolidation on Groq for overnight speed).

**When NOT to use it:** if your base LLM already works well for consolidation,
omit the entire block. The default behavior (`memory.llm` absent)
reuses the agent's provider.

---

## Memory reconciliation — `memory.reconcile_*`

Memory reconciliation is an optional per-agent process that resolves contradictions and redundancies in long-term memory. It complements consolidation: while consolidation *extracts* new memories from conversation history, reconciliation *reviews existing memories* to keep them consistent over time.

**Canonical case:** the agent stored "estoy enfermo, tomo tratamiento X" months ago. After a new conversation, "ya me recuperé" lands in memory. The reconciliation process groups those two entries (cosine similarity ≥ `reconcile_similarity_threshold`) and the LLM decides to `merge` them into a single updated memory that preserves the timeline — soft-deleting the originals.

### How it works

1. `load_unreconciled(agent_id)` — seeds: active memories with `reconciled=0`
2. For each seed: `search_with_scores()` retrieves the `reconcile_top_k` most similar neighbors; entries below `reconcile_similarity_threshold` or outside the same `(channel, chat_id)` scope are discarded
3. The LLM receives the cluster and returns a JSON array of actions:
   - `merge` — creates a new unified memory + soft-deletes the originals
   - `supersede` — soft-deletes outdated entries, keeps the winner
   - `downweight` — reduces relevance of the entry (no delete)
   - `keep` — no-op
4. Actions are applied; processed seeds are marked `reconciled=1` via `mark_reconciled(ids)` — **never globally** (only the seeds of the current run)
5. Entries created by `merge` are born with `reconciled=True` — anti-loop: they won't be re-processed until a new neighbor surfaces in a future run

**Best-effort:** a cluster that fails does not abort the rest (unlike consolidation, which is transactional).

### Enabling per agent

In `agents/{id}.yaml`:

```yaml
memory:
  enabled: true
  reconcile_enabled: true                # activates the feature
  reconcile_schedule: "0 4 * * 1"        # default: Mondays at 4am (user timezone)
  reconcile_similarity_threshold: 0.80   # 0.0–1.0; higher = tighter clusters
  reconcile_top_k: 10                    # neighbors per seed
  reconcile_llm:                         # optional sub-agent
    agent_id: memory_reconciler
```

When `reconcile_enabled: true`, a builtin task `reconcile_memory_{id}` is created automatically in `scheduler.db` on the next startup. Changing `reconcile_schedule` and restarting the daemon is enough to apply the new cron — no manual DB edit needed.

### Dedicated sub-agent (`memory_reconciler`)

By default the agent's own LLM is used with a hardcoded prompt. For higher quality or to avoid consuming the agent's rate limit, you can route reconciliation through a dedicated sub-agent:

1. Copy `config/agents/sub-agents/memory_reconciler.example.yaml` to `config/agents/memory_reconciler.yaml` and adjust the LLM config.
2. Set `memory.reconcile_llm.agent_id: memory_reconciler` on each agent that should use it.

### DB migration

Requires a new column `reconciled INTEGER NOT NULL DEFAULT 0` in the `memories` table of `inaki.db`. The `SQLiteMemoryRepository._ensure_schema()` handles this automatically on first startup — no manual steps needed. If your DB predates this feature, `ALTER TABLE` adds the column with default `0` and all existing memories become seeds for the first reconciliation run.

## Scheduler — `channel_fallback` (channel routing)

The scheduler can schedule tasks from any inbound channel (CLI, REST,
daemon, Telegram). When triggered, the `ChannelRouter` resolves the message's `target`
against a fallback cascade. It never fails due to "unsupported channel":
if nothing matches, the message is written to a hardcoded file.

### Resolution cascade

Given a `target` of the form `<prefix>:<destination>` (e.g. `cli:local`, `telegram:12345`):

1. **Native sink** — if the `prefix` has a registered sink in the container
   (currently: `telegram`), that sink is used directly.
2. **Override** — if `channel_fallback.overrides[<prefix>]` exists,
   it is redirected to the target configured there.
3. **Default** — if `channel_fallback.default` is set, it is redirected there.
4. **Hardcoded** — last resort: `file://~/.inaki/data/scheduler-fallback.log`.
   Always works (creates the file and directory if they don't exist).

### Supported sinks

| Prefix | Description | Example target |
|--------|-------------|----------------|
| `telegram:` | Sends via the registered Telegram bot. | `telegram:12345` |
| `file://` | Appends to a file. Creates parent dir. No sandbox. | `file:///var/log/inaki.log` |
| `null:` | Silently discards. | `null:` |

### Config examples

```yaml
# Example 1: send everything from CLI/REST/daemon to Telegram.
scheduler:
  channel_fallback:
    overrides:
      cli: "telegram:12345"
      rest: "telegram:12345"
      daemon: "telegram:12345"
```

```yaml
# Example 2: uniform default — anything not native goes to a file.
scheduler:
  channel_fallback:
    default: "file:///home/pi/.inaki/data/schedule-output.log"
```

```yaml
# Example 3: silence a specific channel, rest goes to default.
scheduler:
  channel_fallback:
    default: "telegram:99999"
    overrides:
      daemon: "null:"    # daemon does not notify anyone
```

### Traceability

Each send persists in `task_logs.metadata` (JSON) a pair
`{original_target, resolved_target}`. Example query:

```sql
SELECT task_id, metadata FROM task_logs WHERE status = 'success';
-- → {"original_target":"cli:local","resolved_target":"file://~/.inaki/data/scheduler-fallback.log"}
```

Useful for auditing where a message actually ended up when there was a fallback.

### FileSink — line format

```
2026-04-15T03:00:00+00:00 | texto del mensaje
```

One line per send, ISO8601 UTC timestamp. Append-only.

---

## `photos` — Facial recognition pipeline

Controls the processing of photos sent via Telegram: face detection (InsightFace), matching against the registry, scene description (multimodal LLM), and visual annotation.

`photos: null` (by default) disables the entire feature. No model is loaded and `faces.db` is not created.

```yaml
photos:
  enabled: true
  enrollment_chats: private   # private | none

  faces:
    provider: insightface       # only supported provider
    model: buffalo_sc           # buffalo_sc | buffalo_s | buffalo_l
    match_threshold: 0.55       # cosine ≥ threshold → MATCHED
    ambiguous_threshold: 0.40   # between ambiguous and match → AMBIGUOUS

  scene:
    provider: anthropic         # anthropic | openai | groq
    model: claude-haiku-4-5-20251001
    prompt_template: null       # null = built-in prompt in Spanish
    api_key: null               # best placed in global.secrets.yaml

  dedup:
    enabled: true
    schedule: "0 3 * * *"      # cron — nightly deduplication job
    similarity_threshold: 0.70  # similarity between centroids to report a duplicate pair
```

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | If false, the bot ignores photos with a warning. Models are not loaded. |
| `enrollment_chats` | enum | `private` | Chat types where the agent offers to register faces. |

#### `faces.*`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `insightface` | Only supported provider. |
| `model` | enum | `buffalo_sc` | InsightFace model. See model table below. |
| `match_threshold` | float | `0.55` | Cosine similarity score for MATCHED. |
| `ambiguous_threshold` | float | `0.40` | Score for AMBIGUOUS (between ambiguous and match). |

**Available InsightFace models:**

| Model | Size | Accuracy | Recommended for |
|-------|------|----------|-----------------|
| `buffalo_sc` | ~80 MB | Medium | Raspberry Pi 5 (default) |
| `buffalo_s` | ~150 MB | High | Devices with more RAM |
| `buffalo_l` | ~400 MB | Very high | Servers / GPU |

> **Changing `faces.model` invalidates `faces.db`**. Procedure: stop daemon → `rm ~/.inaki/data/faces.db` → restart → re-enroll people. See [`docs/face-recognition.md`](face-recognition.md).

#### `scene.*`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | enum | `anthropic` | Multimodal LLM provider for scene description. |
| `model` | string | `claude-haiku-4-5-20251001` | Provider model. |
| `prompt_template` | string\|null | `null` | Custom prompt. `null` uses the built-in prompt. |
| `api_key` | string\|null | `null` | API key. Best placed in `global.secrets.yaml`. |

#### `dedup.*`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enables the nightly deduplication job. |
| `schedule` | cron | `"0 3 * * *"` | When the job runs (3am by default). |
| `similarity_threshold` | float | `0.70` | Minimum score between centroids to report a duplicate pair. |

### Minimal configuration

```yaml
# global.yaml
photos:
  enabled: true
  scene:
    provider: anthropic
    model: claude-haiku-4-5-20251001

# global.secrets.yaml
photos:
  scene:
    api_key: "sk-ant-..."
```

### Bootstrap

```bash
# First time
systemctl --user stop inaki
# add photos: block in global.yaml
systemctl --user start inaki
# faces.db is created automatically in ~/.inaki/data/faces.db on first use
```

See the full guide at [`docs/face-recognition.md`](face-recognition.md).
