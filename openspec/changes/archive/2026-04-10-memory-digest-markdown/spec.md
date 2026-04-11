# Spec: memory-digest-markdown

**Change:** Replace per-turn `memory.search` vector lookup with a consolidate-time markdown digest  
**Status:** draft  
**Date:** 2026-04-10

---

## 1. Requirements

### Functional Requirements

**FR-01 — Conditional embedding call**  
`embed_query` MUST NOT be called during `RunAgentUseCase.execute` or `inspect` when both `skills_rag_active` and `tools_rag_active` are false. When either flag is true, `embed_query` MUST be called as before.

**FR-02 — Digest file written on consolidate**  
After a successful `ConsolidateMemoryUseCase.execute` run with at least one memory stored, a markdown file MUST exist at the resolved `memory.digest_path` (`~` expanded). The file MUST contain: a `# Recuerdos sobre el usuario` header, a comment line with an ISO8601 UTC timestamp, and up to `memory.digest_size` bullet lines ordered by `created_at DESC`.

**FR-03 — Digest file read on each turn**  
`RunAgentUseCase.execute` MUST read the digest file (if present) and its content MUST appear in the system prompt returned by `AgentContext.build_system_prompt`. If the file does not exist, behavior MUST be equivalent to an empty digest — no crash, no placeholder text.

**FR-04 — `memory.search` removed from hot path**  
`RunAgentUseCase` MUST NOT invoke `IMemoryRepository.search` in `execute` or `inspect`.

**FR-05 — Consolidate preserves archive+clear**  
`ConsolidateMemoryUseCase.execute` MUST still call `history.archive(agent_id)` and `history.clear(agent_id)` at the end of a successful consolidation, in the same order as before.

**FR-06 — Digest sources from existing `memory.get_recent`**  
`ConsolidateMemoryUseCase._write_digest` MUST source the digest memories by calling the existing `IMemoryRepository.get_recent(cfg.memory.digest_size)` method. No new port method is introduced. The ordering (`created_at DESC`) and limit behavior are pre-existing guarantees of `get_recent` (`memory_port.py:18`, `sqlite_memory_repo.py:120`) and are assumed, not re-asserted.

**FR-07 — Markdown format**  
Each bullet line in the digest MUST match: `- [YYYY-MM-DD] {content} ({tag1}, {tag2})`. When tags are absent, the parenthetical MUST be omitted. When `created_at` is missing or unparseable, the date MUST fall back to today (UTC).

**FR-08 — Config fields and `~` expansion**  
`MemoryConfig` MUST expose `digest_size: int` (default `14`) and `digest_path: str` (default `"~/.inaki/mem/last_memories.md"`). The `~` in `digest_path` MUST be expanded via `Path.expanduser()` at load time, before the value is returned to any caller.

**FR-09 — `InspectResult` API update**  
`InspectResult` MUST replace the `memories: list[MemoryEntry]` field with `memory_digest: str`.

**FR-10 — Graceful first run**  
On the first agent run before any `/consolidate` (no digest file, parent directory may or may not exist), `RunAgentUseCase.execute` MUST complete without error and produce a response.

---

### Non-Functional Requirements

**NFR-01 — No per-turn file creation**  
Reading the digest file MUST NOT create the file if it is missing. Only `ConsolidateMemoryUseCase` writes it.

**NFR-02 — Parent dir creation**  
`ConsolidateMemoryUseCase` MUST call `Path.mkdir(parents=True, exist_ok=True)` on the parent directory of `digest_path` before writing.

**NFR-03 — Atomic-ish write**  
The digest MUST be written via `Path.write_text` (or equivalent). A reader encountering malformed content MUST treat it as opaque text — it MUST NOT crash the agent.

**NFR-04 — Existing test suite unaffected**  
No existing test MUST break except those explicitly listed in the Affected Areas of the proposal (`test_run_agent.py`, `test_consolidate_memory.py`, `test_sqlite_memory_repo.py`).

**NFR-05 — `SQLiteMemoryRepository.search` preserved**  
`SQLiteMemoryRepository.search` MUST remain implemented and its existing tests MUST stay green.

---

## 2. Scenarios

### SC-01 — embed_query not called when both RAG flags are false

```
Given RunAgentUseCase with a mock embedder
And skills_rag_active = False, tools_rag_active = False
When execute(agent_id, user_input) is called
Then the mock embedder's embed_query method receives zero calls
```

### SC-02 — embed_query called when at least one RAG flag is true

```
Given RunAgentUseCase with a mock embedder
And skills_rag_active = True (or tools_rag_active = True)
When execute(agent_id, user_input) is called
Then the mock embedder's embed_query method receives at least one call
```

### SC-03 — Digest file written after consolidate with memories

```
Given ConsolidateMemoryUseCase configured with digest_path pointing to a tmp path
And the LLM extractor returns at least one MemoryEntry
When execute(agent_id) is called
Then a file exists at the resolved digest_path
And the file starts with "# Recuerdos sobre el usuario"
And the file contains a comment line matching "<!-- Generado por /consolidate — YYYY-MM-DDTHH:MMZ -->"
And the file contains at most digest_size bullet lines matching "- [YYYY-MM-DD] {content}"
```

### SC-04 — Digest bullets ordered by created_at DESC

```
Given a get_recent response with entries dated 2026-04-08 and 2026-04-09
When ConsolidateMemoryUseCase writes the digest
Then the 2026-04-09 entry appears before the 2026-04-08 entry in the file
```

### SC-05 — Digest content appears in system prompt when file exists

```
Given RunAgentUseCase with a digest file at digest_path containing "- [2026-04-09] Test fact"
When execute(agent_id, user_input) is called
Then the system prompt passed to the LLM contains "- [2026-04-09] Test fact"
```

### SC-06 — No crash when digest file is absent

```
Given RunAgentUseCase and no file at digest_path
When execute(agent_id, user_input) is called
Then no exception is raised
And the system prompt does not contain a placeholder or error text for the missing digest
```

### SC-07 — memory.search not called during execute

```
Given RunAgentUseCase with a mock IMemoryRepository
When execute(agent_id, user_input) is called
Then the mock's search method receives zero calls
```

### SC-08 — memory.search not called during inspect

```
Given RunAgentUseCase with a mock IMemoryRepository
When inspect(agent_id) is called
Then the mock's search method receives zero calls
```

### SC-09 — archive and clear still called after consolidate

```
Given ConsolidateMemoryUseCase with a mock IHistoryStore
When execute(agent_id) completes successfully
Then the mock's archive(agent_id) was called once
And the mock's clear(agent_id) was called once after archive
```

### SC-10 — Consolidate calls get_recent with configured digest_size

```
Given ConsolidateMemoryUseCase with cfg.memory.digest_size = 3
And a mock IMemoryRepository
When execute(agent_id) completes successfully
Then the mock's get_recent method was called exactly once with limit=3
```

### SC-11 — Consolidate passes digest_size through, not the get_recent default

```
Given ConsolidateMemoryUseCase with cfg.memory.digest_size = 20
And a mock IMemoryRepository
When execute(agent_id) completes successfully
Then the mock's get_recent method was called with limit=20 (not the default 10)
```

### SC-12 — Bullet line format with tags

```
Given a MemoryEntry with content="Prefiere español", tags=["preferencias", "idioma"], created_at=2026-04-09
When the digest formatter renders the entry
Then the line is "- [2026-04-09] Prefiere español (preferencias, idioma)"
```

### SC-13 — Bullet line format without tags

```
Given a MemoryEntry with content="Usa LazyVim", tags=[], created_at=2026-04-08
When the digest formatter renders the entry
Then the line is "- [2026-04-08] Usa LazyVim" (no parenthetical)
```

### SC-14 — Bullet line with missing created_at falls back to today

```
Given a MemoryEntry with created_at=None
When the digest formatter renders the entry
Then the date used is today's UTC date in [YYYY-MM-DD] format
```

### SC-15 — MemoryConfig exposes digest_size and digest_path

```
Given a config YAML with "memory:\n  digest_size: 20\n  digest_path: ~/.inaki/mem/x.md"
When loaded into MemoryConfig
Then cfg.digest_size == 20
And cfg.digest_path is the absolute expanded path (not "~/.inaki/mem/x.md")
And "~" does not appear in cfg.digest_path
```

### SC-16 — MemoryConfig defaults

```
Given a config YAML with no digest_size or digest_path under memory
When loaded into MemoryConfig
Then cfg.digest_size == 14
And cfg.digest_path resolves to the user's home directory + "/.inaki/mem/last_memories.md"
```

### SC-17 — InspectResult has memory_digest, not memories

```
Given InspectResult constructed with memory_digest="# Recuerdos..."
Then result.memory_digest == "# Recuerdos..."
And InspectResult does not have a field named "memories"
```

### SC-18 — First run before any consolidate completes without error

```
Given RunAgentUseCase on a clean tmp data directory with no digest file
When execute(agent_id, user_input) is called
Then the call completes without raising any exception
And a response is returned
```

### SC-19 — Parent directory created before digest write

```
Given ConsolidateMemoryUseCase with digest_path under a non-existent directory
When execute(agent_id) is called
Then the parent directory is created (mkdir parents=True)
And the digest file is written successfully
```

### SC-20 — Reader does not crash on malformed digest content

```
Given a digest file at digest_path containing arbitrary non-markdown text
When RunAgentUseCase.execute reads the file
Then no exception is raised
And the raw content is injected into the system prompt as-is
```

---

## 3. Acceptance Criteria

**AC-01** — Unit test in `tests/unit/use_cases/test_run_agent_basic.py` asserts zero `embed_query` calls when both RAG flags are false, and ≥1 call when either is true.

**AC-02** — Unit test in `tests/unit/use_cases/test_run_agent_basic.py` asserts zero `memory.search` calls during `execute` and `inspect`.

**AC-03** — Unit tests in `tests/unit/use_cases/test_run_agent_basic.py` cover digest-present and digest-absent system prompt behavior (SC-05, SC-06).

**AC-04** — Integration-style test in `tests/unit/use_cases/test_consolidate_memory.py` asserts digest file written with correct format, and that `archive`/`clear` are still called.

**AC-05** — Unit tests in `tests/unit/use_cases/test_consolidate_memory.py` assert `memory.get_recent` is called with `limit=cfg.memory.digest_size` (SC-10, SC-11). No new adapter test is added for `get_recent` itself — its ordering and limit contract is covered by the pre-existing adapter test suite.

**AC-06** — Unit tests in `tests/unit/infrastructure/test_config.py` cover `digest_size` / `digest_path` loading and `~` expansion (SC-15, SC-16).

**AC-07** — `InspectResult` type check: field `memory_digest: str` exists; field `memories` does NOT exist.

**AC-08** — Unit tests in `tests/unit/domain/test_agent_context.py` assert `memory_digest` content appears in `build_system_prompt` output.

**AC-09** — Existing `pytest` suite passes; no test broken outside the files listed in AC-01 through AC-08.

**AC-10** — `grep -r "memory.search" core/use_cases/` returns no matches for `run_agent.py`.

---

## 4. Test Matrix

| Requirement | Test File |
|-------------|-----------|
| FR-01 (embed_query conditional) | `tests/unit/use_cases/test_run_agent_basic.py` |
| FR-02 (digest written on consolidate) | `tests/unit/use_cases/test_consolidate_memory.py` |
| FR-03 (digest in system prompt) | `tests/unit/use_cases/test_run_agent_basic.py`, `tests/unit/domain/test_agent_context.py` |
| FR-04 (search removed from hot path) | `tests/unit/use_cases/test_run_agent_basic.py` |
| FR-05 (archive+clear preserved) | `tests/unit/use_cases/test_consolidate_memory.py` |
| FR-06 (consolidate calls get_recent with digest_size) | `tests/unit/use_cases/test_consolidate_memory.py` |
| FR-07 (markdown format) | `tests/unit/use_cases/test_consolidate_memory.py` |
| FR-08 (config fields + ~ expansion) | `tests/unit/infrastructure/test_config.py` |
| FR-09 (InspectResult API) | `tests/unit/use_cases/test_run_agent_basic.py` |
| FR-10 (graceful first run) | `tests/unit/use_cases/test_run_agent_basic.py` |
| NFR-01 (no file creation on read) | `tests/unit/use_cases/test_run_agent_basic.py` |
| NFR-02 (parent dir creation) | `tests/unit/use_cases/test_consolidate_memory.py` |
| NFR-03 (malformed content tolerated) | `tests/unit/use_cases/test_run_agent_basic.py` |
| NFR-04 (existing tests unaffected) | all existing test files |
| NFR-05 (search preserved in adapter) | `tests/unit/adapters/test_sqlite_memory_repo.py` |

---

## 5. Out of Scope — Reference

- **Future LLM memory-search tool**: `SQLiteMemoryRepository.search` is intentionally preserved for a future on-demand tool; this change does not implement that tool.
- **`~/.inaki/` migration for other paths**: `data/`, `models/`, `skills/`, `config/` remain project-relative. Only the digest path moves to the home directory in this change.
- **`inaki.db` cleanup/rotation**: growing database is an accepted risk; cleanup is a separate future change.
- **Refresh-on-startup or `/refresh-digest` command**: the digest is regenerated only at the end of `/consolidate`. No live-refresh mechanism.
- **Changing memory extraction, embedding, or storage**: fact extraction pipeline and `memories` table schema are unchanged.
