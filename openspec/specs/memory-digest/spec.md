# Memory Digest Specification

## Purpose

Replace the per-turn `memory.search` vector lookup with a stable, regenerated markdown digest of the N most recent long-term memories. The digest is written at consolidate time and read on every agent turn. This eliminates embedding calls from the hot path when no RAG mode is active and provides predictable recall of recent memories.

---

## Requirements

### FR-01 — Conditional embedding call

`embed_query` MUST NOT be called during `RunAgentUseCase.execute` or `inspect` when both `skills_rag_active` and `tools_rag_active` are false. When either flag is true, `embed_query` MUST be called as before.

### FR-02 — Digest file written on consolidate

After a successful `ConsolidateMemoryUseCase.execute` run with at least one memory stored, a markdown file MUST exist at the resolved `memory.digest_path` (`~` expanded). The file MUST contain: a `# Recuerdos sobre el usuario` header, a comment line with an ISO8601 UTC timestamp, and up to `memory.digest_size` bullet lines ordered by `created_at DESC`.

### FR-03 — Digest file read on each turn

`RunAgentUseCase.execute` MUST read the digest file (if present) and its content MUST appear in the system prompt returned by `AgentContext.build_system_prompt`. If the file does not exist, behavior MUST be equivalent to an empty digest — no crash, no placeholder text.

### FR-04 — `memory.search` removed from hot path

`RunAgentUseCase` MUST NOT invoke `IMemoryRepository.search` in `execute` or `inspect`.

### FR-05 — Consolidate preserves archive+clear

`ConsolidateMemoryUseCase.execute` MUST still call `history.archive(agent_id)` and `history.clear(agent_id)` at the end of a successful consolidation, in the same order as before.

### FR-06 — Digest sources from existing `memory.get_recent`

`ConsolidateMemoryUseCase._write_digest` MUST source the digest memories by calling the existing `IMemoryRepository.get_recent(cfg.memory.digest_size)` method. No new port method is introduced.

### FR-07 — Markdown format

Each bullet line in the digest MUST match: `- [YYYY-MM-DD] {content} ({tag1}, {tag2})`. When tags are absent, the parenthetical MUST be omitted. When `created_at` is missing or unparseable, the date MUST fall back to today (UTC).

### FR-08 — Config fields and `~` expansion

`MemoryConfig` MUST expose `digest_size: int` (default `14`) and `digest_path: Path` (default `~/.inaki/mem/last_memories.md`). The `~` in `digest_path` MUST be expanded via `Path.expanduser()` at load time, before the value is returned to any caller.

### FR-09 — `InspectResult` API update

`InspectResult` MUST replace the `memories: list[MemoryEntry]` field with `memory_digest: str`.

### FR-10 — Graceful first run

On the first agent run before any `/consolidate` (no digest file, parent directory may or may not exist), `RunAgentUseCase.execute` MUST complete without error and produce a response.

---

## Non-Functional Requirements

### NFR-01 — No per-turn file creation

Reading the digest file MUST NOT create the file if it is missing. Only `ConsolidateMemoryUseCase` writes it.

### NFR-02 — Parent dir creation

`ConsolidateMemoryUseCase` MUST call `Path.mkdir(parents=True, exist_ok=True)` on the parent directory of `digest_path` before writing.

### NFR-03 — Atomic-ish write and reader tolerance

The digest MUST be written via `Path.write_text`. A reader encountering malformed content or encoding errors MUST treat them as opaque and MUST NOT crash the agent. `_read_digest` catches `FileNotFoundError`, `UnicodeDecodeError`, and `OSError`, returning `""` on any failure.

### NFR-04 — Existing test suite unaffected

No existing test MUST break except those explicitly updated to reflect the new conditional-RAG contract.

### NFR-05 — `SQLiteMemoryRepository.search` preserved

`SQLiteMemoryRepository.search` MUST remain implemented and its existing tests MUST stay green. It is reserved for a future on-demand memory tool.

---

## Key Architectural Decisions

1. **No new port**: `Path.read_text`/`Path.write_text` live directly in the use cases. The existing `IMemoryRepository.get_recent(limit)` is reused as-is.
2. **Digest written BEFORE `history.archive`/`clear`**: fail-safer ordering — a failed archive still leaves a correct digest; the inverse would leave a stale digest after a successful archive.
3. **`memory_digest` is a plain `str`** in `AgentContext`, concatenated verbatim. The digest file owns its own header; `AgentContext` does NOT wrap it.
4. **`memory.digest_path` lives under `~/.inaki/mem/`**: first concrete application of the user-data separation principle. Future user-owned paths should also migrate to `~/.inaki/`.
5. **Pydantic v2 `field_validator(mode="before")` does NOT run on class-level defaults**: `model_post_init` is required in addition to `field_validator` to expand `~` for the default value of `digest_path`.

---

## Out of Scope

- Future LLM tool that lets the agent search old memories on-demand (SQLiteMemoryRepository.search is deliberately preserved).
- General migration of user-owned paths to `~/.inaki/` (only digest path moves in this change).
- `inaki.db` cleanup/rotation (accepted risk).
- Refresh-on-startup or `/refresh-digest` command (digest is regenerated only at `/consolidate`).
