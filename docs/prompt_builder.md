# Prompt Builder — Final Prompt Construction

## Overview

The prompt the LLM receives on each turn **is not static**. It is built dynamically at runtime by combining:

1. The agent's base system prompt (defined in its YAML)
2. Relevant memories retrieved via vector search
3. Relevant skills selected via semantic routing
4. The schemas of selected tools (filtered or not by semantic routing)

The conversation history is sent as a separate message list from the system prompt, **truncated to the configured maximum** before being sent to the LLM.

---

## Full Construction Flow

```
RunAgentUseCase.execute(user_input)
│
├── 1. _history.load(agent_id, channel, chat_id, limit=max_messages)
│       → list[Message]  ← scoped history from SQLite (history.db)
│       → only the last N messages (if max_messages > 0)
│       → scoped by (agent_id, channel, chat_id)
│
├── 2. _embedder.embed_query(user_input)
│       → query_vec: list[float]
│
├── 3. _memory.search(query_vec, top_k)
│       → list[MemoryEntry]  ← relevant memories (cosine sim in SQLite)
│
├── 4. _skills.list_all() → all_skills
│   ├── If len(all_skills) > cfg.skills.semantic_routing_min_skills:
│   │       _skills.retrieve(query_vec, top_k=cfg.skills.semantic_routing_top_k)
│   │       → list[Skill]  ← only relevant skills
│   └── Otherwise:
│           retrieved_skills = all_skills  ← all skills
│
├── 5. AgentContext.build_system_prompt(base_prompt)
│       → system_prompt: str  ← joined sections + substitution of {{WORKSPACE}}, {{DATE}}, etc.
│
├── 6. _tools.get_schemas() → all_schemas
│   ├── If len(all_schemas) > cfg.tools.semantic_routing_min_tools:
│   │       _tools.get_schemas_relevant(query_vec, top_k=cfg.tools.semantic_routing_top_k)
│   │       → tool_schemas: list[dict]  ← only relevant tools
│   └── Otherwise:
│           tool_schemas = all_schemas  ← all tools
│
└── 7. _llm.complete(messages, system_prompt, tools=tool_schemas)
        ↑                ↑                          ↑
    truncated        dynamic prompt          filtered schemas
    history
```

---

## History Truncation for the Prompt

`chat_history.max_messages` controls how many messages are injected into the prompt. Truncation is applied directly in the SQL query (`LIMIT`).

```
max_messages = 21  →  SELECT ... ORDER BY id DESC LIMIT 21
                      → last 21 messages from the scope (agent_id, channel, chat_id)

max_messages = 0   →  no truncation, full history for the scope
```

Configurable in `global.yaml`:

```yaml
chat_history:
  db_filename: "data/history.db"
  max_messages: 21  # 0 = no limit
```

---

## System Prompt Construction (`AgentContext.build_system_prompt`)

**File:** `core/domain/value_objects/agent_context.py`

The prompt is built by concatenating sections. Only sections with content are included:

```
[base_prompt]

## What you remember about the user:         ← only if there are memories
- <memory 1>
- <memory 2>
- ...

## Available skills:                          ← only if there are skills
- **<name>**: <description>
  <instructions>
```

### Example of a generated final prompt

```
Sos Inaki, un asistente personal ágil y directo.
Respondés en español rioplatense. Usás las tools cuando hace falta.

## Lo que recuerdas del usuario:
- El usuario trabaja principalmente con Python y prefiere respuestas concisas
- El usuario tiene un servidor Raspberry Pi 5 con Ubuntu

## Skills disponibles:
- **Búsqueda Web**: Busca información en internet usando DuckDuckGo
  Cuando el usuario pregunta sobre eventos actuales, usá esta skill...
```

### Variable Substitution (`{{...}}`)

After concatenating all sections (base prompt, user context, memory digest, skill blocks, and `extra_sections`), a substitution pass is run over the **complete text** (`_resolve_vars` in the same module). Matches use the `{{NAME}}` form and are **case-insensitive** (`{{date}}` is equivalent to `{{DATE}}`).

| Placeholder | Result | Source and Notes |
|-------------|--------|------------------|
| `{{WORKSPACE}}` | Absolute path of the agent's working directory | Same resolution as filesystem tools: `Path(workspace.path).expanduser().resolve()` from the config. If no root is injected (exceptional case), the text **is not modified**. |
| `{{TIMEZONE}}` | Timezone label | If `AgentContext.timezone` is a valid IANA zone (e.g., `Europe/Madrid`), that name is shown. If empty or invalid, the system's local abbreviation (`%Z`) is used when computing the time. |
| `{{DATETIME}}` | Local date and time | Fixed format `YYYY-MM-DD HH:MM` in the already-resolved zone (IANA from context or fallback to the host's local clock if the IANA fails). |
| `{{DATE}}` | Local date | `YYYY-MM-DD`. |
| `{{TIME}}` | Local time | `HH:MM` (24h). |
| `{{WEEKDAY}}` | Day of the week name | Without suffix: `strftime("%A")` per **system locale**. With two-letter language: `{{WEEKDAY[EN]}}`, `{{WEEKDAY[ES]}}`, `{{WEEKDAY[FR]}}` → fixed names in English, Spanish, or French. Any other code (e.g., `{{WEEKDAY[DE]}}`) is treated as no flag (same fallback as locale). The flag is case-insensitive (`[en]`, `[FR]`). |
| `{{WEEKDAY_NUMBER}}` | ISO 8601 day | String `1`–`7`: Monday = 1, …, Sunday = 7. |

**Context timezone:** `RunAgentUseCase` fills `AgentContext.timezone` with the user preference (global config); if there is no useful value, the date/time placeholders use the process's local timezone.

**Any other `{{SOMETHING}}`** that doesn't match the table above **is left as-is** in the prompt (there is no generic template engine).

Valid examples in the agent's YAML:

```text
Hoy es {{WEEKDAY[ES]}} {{DATE}} ({{TIME}}). Tu workspace es {{WORKSPACE}}.
Zona configurada: {{TIMEZONE}}
```

---

## What Gets Sent to the LLM

The final call to `llm.complete()` receives three pieces:

| Parameter | Content | Source |
|-----------|---------|--------|
| `messages` | Truncated history + current user message | `SQLiteHistoryStore` → `load(limit=max_messages)` → `+ user_msg` |
| `system_prompt` | Base + memories + skills | `AgentContext.build_system_prompt()` |
| `tools` | JSON schemas of selected tools | `ToolRegistry.get_schemas[_relevant]()` |

---

## Skill Selection via Semantic Routing

> This is NOT RAG — it is dynamic selection of capabilities (available skills/tools). Real RAG (external knowledge retrieval) is configured under `knowledge:`.

```
len(all skills) > skills.semantic_routing_min_skills (default: 5)
│
├── YES → retrieve(query_vec, top_k=semantic_routing_top_k)
│         Cosine similarity between query_vec and pre-indexed embeddings of each skill
│         → Only the top_k most relevant skills for the current message
│
└── NO → list_all() → all skills unfiltered
```

```yaml
skills:
  semantic_routing_min_skills: 5
  semantic_routing_top_k: 3
```

---

## Tool Selection via Semantic Routing

```
len(all tools) > tools.semantic_routing_min_tools (default: 10)
│
├── YES → get_schemas_relevant(query_vec, top_k=semantic_routing_top_k)
│         Cosine similarity between query_vec and each tool.description embedding
│         → Only the top_k most relevant tools for the current message
│
└── NO → get_schemas() → all tools unfiltered
```

```yaml
tools:
  semantic_routing_min_tools: 10
  semantic_routing_top_k: 5
  tool_call_max_iterations: 5  # maximum retries in the tool call loop
```

---

## Embedding Lifecycle

| Embedding | When Computed | Who Computes It | Purpose |
|-----------|---------------|-----------------|---------|
| `embed_query(user_input)` | Each turn | `RunAgentUseCase` | Search for relevant memories, skills, and tools |
| `embed_passage(skill description)` | On startup (lazy) | `YamlSkillRepository` | Skill index |
| `embed_passage(tool.description)` | Before the first tool semantic routing (lazy) | `ToolRegistry` | Tool index |
| `embed_passage(fact.content)` | During consolidation | `ConsolidateMemoryUseCase` | Save long-term memory |

Skill and tool embeddings are computed **once** on first use and cached in memory. They are not persisted to disk.

---

## History: What Gets Saved and What Doesn't

Only `user` and `assistant` messages are persisted in `history.db` (`history` table, scoped by `agent_id, channel, chat_id`).

Tool call and tool result messages are **ephemeral** — they exist only in `working_messages` during the execution loop and are never persisted to history.

```
Persisted in history.db:        Only in memory during the turn:
───────────────────────         ──────────────────────────────────
role=user: ...                  tool_call: { name: "shell", args: ... }
role=assistant: ...             tool_result: "[shell]: output..."
role=user: ...
role=assistant: ...
```

---

## How to Inspect the Prompt at Runtime

```bash
# One-shot from terminal
inaki inspect "busca el precio del dolar"
inaki inspect "ejecuta los tests" --agent dev

# Interactive inside CLI chat
/inspect busca el precio del dolar
```

The `inspect` command runs the complete pipeline (embedding → memory search → semantic routing → truncation → prompt construction → tool selection) **without calling the LLM or persisting anything**, and prints:

- Retrieved memories
- Selected skills (with indication of whether skill semantic routing is active)
- Tools sent to the LLM (with indication of whether tool semantic routing is active)
- Complete final system prompt
