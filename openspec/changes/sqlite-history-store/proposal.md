# Proposal: sqlite-history-store

**Status**: Proposed
**Date**: 2026-04-09
**Change ID**: sqlite-history-store

---

## Problem

`FileHistoryStore` (`adapters/outbound/history/file_history_store.py`) stores conversation
history as plain `.txt` files (one file per agent under `data/history/active/`).
Three concrete issues drive this change:

1. **Cold-start read amplification.** On process restart the in-memory deque cache is empty.
   The first `load()` call reads the *entire* file just to warm a window of the last N
   messages. For long conversations this is wasteful IO.

2. **`Message` has no timestamp.** `core/domain/entities/message.py` defines
   `Message(role, content)` with no temporal field. Long-term memories extracted during
   consolidation are stamped with `datetime.now()` at consolidation time — not at the time
   the conversation actually happened. This loses temporal context for long-term memory.

3. **Non-atomic archive/clear.** `archive()` is a file rename; `clear()` is a file delete.
   A crash between the two leaves the store in an inconsistent state.

---

## Solution

Replace `FileHistoryStore` with `SQLiteHistoryStore` using a new dedicated database
(`data/history.db`, separate from `data/inaki.db` which requires the `sqlite-vec`
extension). Extend `Message` with an optional `timestamp` field. Propagate message
timestamps through the consolidation use case into `MemoryEntry.created_at`.

---

## Scope

### 1. `core/domain/entities/message.py` — add timestamp

```python
timestamp: datetime | None = None
```

Optional field with default `None` — fully backwards-compatible.

### 2. `adapters/outbound/history/sqlite_history_store.py` — NEW

**DB file**: `data/history.db` (never loads `sqlite-vec`).

**Schema**:
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

**Method mapping**:

| Port method | Behaviour |
|---|---|
| `append(agent_id, msg)` | `INSERT` (USER/ASSISTANT only); sets `msg.timestamp` from `created_at` if not already set |
| `load(agent_id)` | `SELECT … WHERE agent_id=? AND archived=0 ORDER BY id DESC LIMIT N`; result returned ASC |
| `load_full(agent_id)` | `SELECT … WHERE agent_id=? AND archived=0 ORDER BY id ASC` |
| `archive(agent_id)` | `UPDATE history SET archived=1 WHERE agent_id=? AND archived=0` |
| `clear(agent_id)` | `DELETE FROM history WHERE agent_id=?` |

No in-memory cache required. Schema initialised lazily on first connection.

### 3. `infrastructure/config.py` — simplify HistoryConfig

Remove `active_dir` and `archive_dir`. Add:
```python
db_path: str = "data/history.db"
```

### 4. `core/use_cases/consolidate_memory.py` — timestamp propagation

**History formatting** — include timestamps when present:
```
user [2026-04-09T15:30:00Z]: content
assistant [2026-04-09T15:31:00Z]: content
user: content   # timestamp=None → bracket omitted
```

**Extractor JSON schema** — add optional `timestamp` field:
```json
[
  {
    "content": "...",
    "relevance": 0.9,
    "tags": ["tag1"],
    "timestamp": "2026-04-09T15:30:00Z"
  }
]
```

**`MemoryEntry.created_at`** — set from extracted `timestamp` when present and parseable;
fall back to `datetime.now(UTC)` otherwise.

### 5. `adapters/outbound/history/file_history_store.py` — DELETE

### 6. `infrastructure/container.py` — wire new store

### 7. Tests

Delete `tests/unit/adapters/test_file_history_store.py`.
Create `tests/unit/adapters/test_sqlite_history_store.py`.

---

## Files Affected

| File | Action |
|---|---|
| `core/domain/entities/message.py` | Modify — add `timestamp: datetime \| None = None` |
| `adapters/outbound/history/file_history_store.py` | Delete |
| `adapters/outbound/history/sqlite_history_store.py` | Create |
| `infrastructure/config.py` | Modify — replace dir fields with `db_path` |
| `core/use_cases/consolidate_memory.py` | Modify — timestamp format + parse |
| `infrastructure/container.py` | Modify — wire `SQLiteHistoryStore` |
| `tests/unit/adapters/test_file_history_store.py` | Delete |
| `tests/unit/adapters/test_sqlite_history_store.py` | Create |

---

## Risks

1. **YAML config breakage.** Any `config/*.yaml` with `history.active_dir` or
   `history.archive_dir` will fail Pydantic validation. All agent YAML files must be
   audited and updated in the same PR.

2. **`archive()` return value semantics.** The port declares `archive → str` (a file path).
   With soft-delete there is no file path. Returns a confirmation string instead.
   `consolidate_memory.py` must be updated accordingly.

3. **Silent data abandonment.** Existing `.txt` files in `data/history/active/` are not
   migrated. Any live history at deploy time will be silently lost. A migration script is
   out of scope.

---

## Non-Goals

- Migrating existing `.txt` history files to SQLite
- Changing `IHistoryStore` port method signatures (beyond `archive` return clarification)
- Any change to `SQLiteMemoryRepository` or `data/inaki.db`

---

## Dependencies

- `aiosqlite` — already in `pyproject.toml`. No new packages required.
