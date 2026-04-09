# Spec: sqlite-history-store

**Change:** Replace `FileHistoryStore` (TXT-based) with `SQLiteHistoryStore`  
**Status:** draft  
**Date:** 2026-04-09

---

## 1. Requirements

### Functional Requirements

**FR-01 — SQLite persistence**  
The system MUST persist conversation history in a SQLite database. Each message row MUST contain: `id`, `agent_id`, `role`, `content`, `created_at` (ISO8601 UTC), `archived` (0/1 integer flag).

**FR-02 — Schema creation on startup**  
`SQLiteHistoryStore.__init__` MUST create the `history` table and the `idx_history_agent` index if they do not already exist. No external migration tool required.

```sql
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_history_agent ON history(agent_id, archived);
```

**FR-03 — Message timestamp**  
`Message` entity MUST gain an optional `timestamp: datetime | None = None` field. The field MUST default to `None` to keep all existing call-sites unchanged (non-breaking addition).

**FR-04 — append**  
`append(agent_id, msg)` MUST:
- Persist only `Role.USER` and `Role.ASSISTANT` messages; silently ignore all others.
- Derive `created_at` from `msg.timestamp` if set; otherwise use `datetime.now(UTC)`.
- Store `created_at` as ISO8601 UTC string (`YYYY-MM-DDTHH:MM:SSZ`).

**FR-05 — load (windowed)**  
`load(agent_id)` MUST:
- Return only `archived = 0` rows for the given `agent_id`, ordered ASC by `id`.
- When `max_messages_in_prompt > 0`: return only the last `max_messages_in_prompt` rows (tail slice).
- When `max_messages_in_prompt = 0`: return all active rows.
- Reconstruct each row into a `Message` with `role`, `content`, and `timestamp` populated.

**FR-06 — load_full**  
`load_full(agent_id)` MUST return ALL active (`archived = 0`) rows for the agent, ordered ASC by `id`, with no windowing. Used exclusively by consolidation.

**FR-07 — archive (soft-delete)**  
`archive(agent_id)` MUST:
- Execute `UPDATE history SET archived = 1 WHERE agent_id = ? AND archived = 0`.
- Return a human-readable confirmation string (e.g. `"Historial de 'agent_id' archivado."`).
- Raise `HistoryError` if no active rows exist for the agent.
- NOT return a file path (breaking change from `FileHistoryStore` — callers must be updated).

**FR-08 — clear (hard-delete)**  
`clear(agent_id)` MUST execute `DELETE FROM history WHERE agent_id = ?` (all rows, active and archived).

**FR-09 — No in-memory cache**  
`SQLiteHistoryStore` MUST NOT implement an in-memory deque cache. SQLite's own page cache is sufficient; removing the cache eliminates cache-invalidation bugs.

**FR-10 — HistoryConfig migration**  
`HistoryConfig` MUST remove `active_dir` and `archive_dir` fields and add `db_path: str = "data/history.db"`. All YAML config files that reference `history.active_dir` or `history.archive_dir` MUST be updated.

**FR-11 — Container wiring**  
`infrastructure/container.py` MUST instantiate `SQLiteHistoryStore` instead of `FileHistoryStore`. `FileHistoryStore` MUST be deleted.

**FR-12 — Consolidation history format**  
`consolidate_memory.py` MUST format each message in the history text as:
- `user [2026-04-09T15:30:00Z]: content` when `msg.timestamp` is set.
- `user: content` when `msg.timestamp` is `None` (backward-compatible fallback).

**FR-13 — Extractor JSON schema extended**  
The LLM extractor prompt MUST request a `timestamp` field in the JSON response:
```json
{"content": "...", "relevance": 0.9, "tags": [...], "timestamp": "2026-04-09T15:30:00Z"}
```
`timestamp` is optional in the response; if absent or unparseable, `MemoryEntry.created_at` MUST default to `datetime.now(UTC)`.

**FR-14 — MemoryEntry.created_at from timestamp**  
When the LLM returns a valid ISO8601 timestamp in the extracted fact, `MemoryEntry.created_at` MUST be set to that parsed datetime (UTC). Otherwise fall back to `datetime.now(UTC)`.

**FR-15 — FileHistoryStore deletion**  
`adapters/outbound/history/file_history_store.py` and its test file MUST be deleted from the repository.

---

### Non-Functional Requirements

**NFR-01 — Async I/O**  
All public methods MUST be `async`. Database access MUST use `aiosqlite` to avoid blocking the event loop.

**NFR-02 — IHistoryStore contract**  
`SQLiteHistoryStore` MUST implement `IHistoryStore` without changing any method signatures. The port (`history_port.py`) docstring for `archive` MUST be updated to reflect that it returns a confirmation string, not a file path.

**NFR-03 — Error handling**  
All `aiosqlite` exceptions MUST be caught and re-raised as `HistoryError` with a descriptive message including `agent_id`.

**NFR-04 — Thread safety**  
The store MUST support concurrent async access from multiple coroutines. Using a single `aiosqlite` connection with WAL mode enabled is acceptable; connection-per-call is also acceptable.

**NFR-05 — No external migration**  
The schema creation (`CREATE TABLE IF NOT EXISTS`) runs automatically on first instantiation. No Alembic, no migration scripts.

**NFR-06 — Test coverage**  
All public methods of `SQLiteHistoryStore` MUST have unit tests using an in-memory SQLite database (`:memory:`). The old `test_file_history_store.py` MUST be deleted and replaced with `test_sqlite_history_store.py`.

---

## 2. Scenarios

### SC-01 — append stores a user message with timestamp

```
Given a SQLiteHistoryStore initialized with db_path ":memory:"
And a Message(role=USER, content="Hola", timestamp=datetime(2026,4,9,15,30,0, tzinfo=UTC))
When append("agent1", message) is called
Then a row is inserted with agent_id="agent1", role="user", content="Hola",
     created_at="2026-04-09T15:30:00Z", archived=0
```

### SC-02 — append stores a message without timestamp (auto-assigns UTC now)

```
Given a SQLiteHistoryStore
And a Message(role=ASSISTANT, content="Buenos días", timestamp=None)
When append("agent1", message) is called
Then a row is inserted with created_at being the current UTC time in ISO8601 format
And the row has archived=0
```

### SC-03 — append ignores non-user/assistant roles

```
Given a SQLiteHistoryStore
When append("agent1", Message(role=SYSTEM, content="...")) is called
Then no row is inserted into the history table
When append("agent1", Message(role=TOOL, content="...")) is called
Then no row is inserted
```

### SC-04 — load returns windowed history ASC

```
Given a SQLiteHistoryStore with max_messages_in_prompt=3
And 5 messages appended for "agent1" (m1..m5)
When load("agent1") is called
Then it returns [m3, m4, m5] in ASC order
And each Message has role, content, and timestamp populated
```

### SC-05 — load returns full history when max_messages_in_prompt=0

```
Given a SQLiteHistoryStore with max_messages_in_prompt=0
And 5 messages appended for "agent1"
When load("agent1") is called
Then it returns all 5 messages in ASC order
```

### SC-06 — load returns empty list for unknown agent

```
Given a SQLiteHistoryStore
When load("unknown_agent") is called
Then it returns []
```

### SC-07 — load excludes archived rows

```
Given a SQLiteHistoryStore
And 3 messages appended then archive("agent1") called
And 2 new messages appended after archiving
When load("agent1") is called
Then only the 2 new (non-archived) messages are returned
```

### SC-08 — load_full returns complete active history

```
Given a SQLiteHistoryStore with max_messages_in_prompt=3
And 10 messages appended for "agent1"
When load_full("agent1") is called
Then it returns all 10 messages in ASC order (windowing ignored)
```

### SC-09 — archive soft-deletes active rows and returns confirmation

```
Given a SQLiteHistoryStore
And 3 messages appended for "agent1"
When archive("agent1") is called
Then all 3 rows have archived=1 in the database
And the return value is a non-empty string (confirmation message, not a file path)
And subsequent load("agent1") returns []
```

### SC-10 — archive raises HistoryError when no active history exists

```
Given a SQLiteHistoryStore with no rows for "agent1"
When archive("agent1") is called
Then HistoryError is raised
```

### SC-11 — archive raises HistoryError when all rows already archived

```
Given a SQLiteHistoryStore
And 2 messages appended and then archive("agent1") already called once
When archive("agent1") is called again
Then HistoryError is raised
```

### SC-12 — clear hard-deletes all rows (active and archived)

```
Given a SQLiteHistoryStore
And 3 messages appended and archived, plus 2 new active messages
When clear("agent1") is called
Then all 5 rows are deleted from the database
And load("agent1") returns []
And load_full("agent1") returns []
```

### SC-13 — clear on unknown agent does not raise

```
Given a SQLiteHistoryStore with no rows for "agent2"
When clear("agent2") is called
Then no exception is raised
```

### SC-14 — multiple agents are isolated

```
Given a SQLiteHistoryStore
And messages appended for "agent1" and "agent2"
When load("agent1") is called
Then only agent1's messages are returned
When archive("agent2") is called
Then only agent2's rows are archived; agent1's rows remain active
```

### SC-15 — consolidation formats messages with timestamps

```
Given a ConsolidateMemoryUseCase
And load_full returns [Message(USER, "text", timestamp=datetime(2026,4,9,15,30,0,UTC))]
When execute() is called
Then the history string passed to the LLM contains "user [2026-04-09T15:30:00Z]: text"
```

### SC-16 — consolidation formats messages without timestamps

```
Given a ConsolidateMemoryUseCase
And load_full returns [Message(USER, "text", timestamp=None)]
When execute() is called
Then the history string contains "user: text" (no timestamp bracket)
```

### SC-17 — consolidation sets MemoryEntry.created_at from LLM timestamp

```
Given a ConsolidateMemoryUseCase
And the LLM returns JSON with timestamp "2026-04-09T15:30:00Z"
When execute() is called
Then the MemoryEntry saved has created_at = datetime(2026,4,9,15,30,0, tzinfo=UTC)
```

### SC-18 — consolidation falls back to now() when LLM timestamp absent

```
Given a ConsolidateMemoryUseCase
And the LLM returns JSON without a timestamp field
When execute() is called
Then MemoryEntry.created_at is set to approximately datetime.now(UTC)
```

### SC-19 — HistoryConfig accepts db_path, rejects legacy fields

```
Given a YAML fragment: "history:\n  db_path: data/history.db\n  max_messages_in_prompt: 21"
When loaded into HistoryConfig via Pydantic
Then cfg.db_path == "data/history.db"
And cfg does NOT have attributes active_dir or archive_dir
```

### SC-20 — YAML configs with legacy history fields cause validation error

```
Given a global.yaml containing "history.active_dir" or "history.archive_dir"
When AgentConfig or GlobalConfig is instantiated
Then Pydantic raises a ValidationError (extra fields forbidden) or the fields are silently ignored
```
*(Exact behavior depends on Pydantic model config — the field MUST NOT be accessible at runtime)*

---

## 3. Acceptance Criteria

**AC-01** — All tests in `tests/unit/adapters/test_sqlite_history_store.py` pass, covering SC-01 through SC-14.

**AC-02** — `tests/unit/use_cases/test_consolidate_memory.py` includes scenarios SC-15 through SC-18 and passes.

**AC-03** — `HistoryConfig` has `db_path` and `max_messages_in_prompt` only; `active_dir` and `archive_dir` are gone.

**AC-04** — `config/global.yaml` and all agent YAML files do NOT reference `history.active_dir` or `history.archive_dir`.

**AC-05** — `adapters/outbound/history/file_history_store.py` does NOT exist in the repository.

**AC-06** — `tests/unit/adapters/test_file_history_store.py` does NOT exist in the repository.

**AC-07** — `SQLiteHistoryStore` implements `IHistoryStore` (`isinstance` check passes).

**AC-08** — `container.py` wires `SQLiteHistoryStore`; no import of `FileHistoryStore` anywhere in the codebase.

**AC-09** — `Message` entity has `timestamp: datetime | None = None`; existing instantiation without `timestamp` continues to work unchanged.

**AC-10** — `IHistoryStore.archive` docstring states it returns a confirmation string, not a file path.

**AC-11** — `ConsolidateMemoryUseCase.execute()` return message no longer includes a file path (it was `"✓ N recuerdo(s)... Historial archivado en <path>"`); it MUST reflect the new confirmation string from `archive()`.

---

## 4. Out of Scope

- **Migration of existing `.txt` history files** — pre-existing TXT files will NOT be imported into SQLite. They remain on disk and are ignored.
- **Multi-database or sharded SQLite** — single `db_path` only; no per-agent databases.
- **Connection pooling beyond aiosqlite defaults** — no external pool (e.g., SQLAlchemy, asyncpg).
- **Encryption of the SQLite file** — plain SQLite; encryption is an infrastructure concern outside this change.
- **Pruning or TTL of archived rows** — archived rows stay indefinitely; no cleanup job.
- **Rollback of archived rows** — soft-delete is one-way; no `unarchive` operation.
- **IHistoryStore signature changes** — port interface method signatures are unchanged; only the docstring for `archive` is updated.
- **Alembic or any migration framework** — schema is self-bootstrapping via `CREATE TABLE IF NOT EXISTS`.
- **Channel adapters or CLI changes** — no changes to how history is surfaced to end users beyond the conversation flow.
