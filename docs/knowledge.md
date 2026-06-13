# Knowledge — How to Give Inaki Knowledge

This document explains how to add external knowledge sources to the agent. For the full YAML parameter reference see `docs/configuracion.md`.

---

## Key Concepts

Inaki has two ways of "remembering" things:

| Mechanism | What It Is | When It's Used |
|-----------|------------|----------------|
| **Memory** | User facts learned during conversations | Automatic, always active |
| **Knowledge** | Documents or databases provided by the user | Requires explicit configuration |

Knowledge sources are queried on each turn (automatic pre-fetch) and are also available via the `knowledge_search` tool for explicit searches.

---

## Case 1 — I Have a Folder with Documents

The most common case: you have `.md`, `.txt`, or `.pdf` files and you want Inaki to understand them.

**Step 1 — Configure the source in `~/.inaki/config/global.yaml`:**

```yaml
knowledge:
  sources:
    - id: "mis-docs"
      type: document
      path: ~/documentos/proyecto/
      glob: "**/*.md"
```

**Step 2 — Index:**

```bash
inaki knowledge index mis-docs
```

Expected output:
```
Indexing source 'mis-docs'...
  Indexed 12 files, 48 chunks
Done.
```

**Step 3 — Verify:**

```bash
inaki knowledge list          # shows all sources and their status
inaki knowledge stats mis-docs  # files, chunks, last indexing, dimension
```

From here on, in every conversation Inaki retrieves the most relevant fragments for the current question and injects them into the context before responding.

### Supported Formats

| Format | Chunking Strategy |
|--------|-------------------|
| `.md`  | Split by headers (`#`/`##`/`###`), sliding window within each section |
| `.txt` | Pure sliding window |
| `.pdf` | Page-by-page extraction, sliding window over the total text |
| other  | Pure sliding window |

### Updating the Index

Indexing is **incremental**: it only re-processes files whose `mtime` changed since the last run. To add or update a document, simply:

1. Copy or modify the file in the configured folder
2. Run `inaki knowledge index <id>` again

The index is stored in `~/.inaki/knowledge/<id>.db` — not in the project.

---

## Case 2 — I Have My Own SQLite Database

If you already have embeddings computed in SQLite (for example, generated with another pipeline), you can connect it directly without Inaki re-indexing it.

```yaml
knowledge:
  sources:
    - id: "mi-base"
      type: sqlite
      path: ~/data/knowledge.db
```

Inaki **does not write** to this DB — it only queries it. The DB must have the schema Inaki expects (`chunks` table + `chunk_embeddings` virtual table with 384-dimension vectors). See `docs/configuracion.md` for the exact schema and an insertion example.

**Critical requirement**: embeddings must be 384 dimensions (e5-small ONNX or equivalent). If the DB uses a different dimension, the source fails on startup with a clear error in the logs.

---

## Case 3 — Custom Source via Extension

If neither of the two previous types works (for example, you want to query an external API, a PostgreSQL DB, or an Elasticsearch index), you can implement your own source in `ext/`:

```python
# ext/mi_extension/manifest.py

def _build_mi_fuente(agent_config, global_config, embedder):
    from mi_extension.fuente import MiFuente
    return MiFuente(embedder=embedder)

KNOWLEDGE_SOURCES = [_build_mi_fuente]
```

The factory receives `(agent_config, global_config, embedder)` and must return an object that implements `IKnowledgeSource` (`core/ports/outbound/knowledge_port.py`). If the factory raises an exception, it is logged as WARNING and the remaining sources continue working.

The guaranteed registration order is: **(1) memory** → **(2) config sources** → **(3) extension sources**.

---

## Can I Send a Document Directly in Chat?

That mechanism does not exist today. You cannot paste a `.md` in the chat and have it indexed. The flow is always:

```
Copy file to the folder → inaki knowledge index <id> → active querying
```

A `knowledge_add_document` tool that automates this would be a natural extension of the current pipeline but is not implemented.

---

## Pre-fetch Control

By default Inaki performs an automatic pre-fetch on each turn. You can adjust it:

```yaml
knowledge:
  enabled: false          # Disables automatic pre-fetch.
                          # Sources remain available via knowledge_search.
  top_k_per_source: 3     # Maximum fragments per source.
  min_score: 0.5          # Minimum similarity score (0.0 – 1.0).
  max_total_chunks: 10    # Total fragment cap after fan-out.
```

If `enabled: false`, pre-fetch is skipped but the user can still invoke `knowledge_search` explicitly to search the sources.

---

## Reference Files

| Role | File |
|------|------|
| Full YAML reference | `docs/configuracion.md` — `knowledge:` section |
| `IKnowledgeSource` port | `core/ports/outbound/knowledge_port.py` |
| Document adapter | `adapters/outbound/knowledge/document_knowledge_source.py` |
| SQLite adapter | `adapters/outbound/knowledge/sqlite_knowledge_source.py` |
| Explicit search tool | `adapters/outbound/tools/knowledge_search_tool.py` |
| Management CLI | `inaki/knowledge_cli.py` |
