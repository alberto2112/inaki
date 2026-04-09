# Design: sqlite-history-store

## Overview

Replace `FileHistoryStore` (text files, one per agent) with `SQLiteHistoryStore` (single SQLite DB, one row per message). The new adapter mirrors the `SQLiteMemoryRepository` connection pattern but uses a dedicated database file to avoid interference with the sqlite-vec extension required by the memory repo.

---

## 1. Architecture Decisions

### 1.1 Separate DB file (`data/history.db` vs `data/inaki.db`)

`SQLiteMemoryRepository` connects to `data/inaki.db` and loads the `sqlite-vec` extension immediately after `connect()`. This is a non-negotiable requirement of the virtual table. Sharing the same file would force `SQLiteHistoryStore` to also load the extension (or fail at open time if the extension is already locked). Using a separate file (`data/history.db`) keeps the two adapters independent, allows different WAL/journal settings in the future, and removes the extension-loading concern entirely.

### 1.2 Soft-delete for `archive` (not file rename)

`FileHistoryStore` moves the active file to `archive/`. With SQLite, the equivalent hard-delete would be a `DELETE` + `INSERT INTO archive_history …`. Instead, an `archived` flag (0/1 integer column) is used because:

- No second table needed — the schema stays minimal.
- Archived rows remain queryable for audit or future recall features without schema changes.
- The operation is a single `UPDATE` statement — atomic by default.
- The return value of `archive()` changes from a filesystem path to a sentinel string (`"sqlite:history:{agent_id}"`). Callers (`ConsolidateMemoryUseCase`) only log this value — they do not interpret it.

### 1.3 No in-memory cache

`FileHistoryStore` has a `deque`-based cache because file I/O on every `load()` call is expensive. SQLite queries with an indexed lookup (`agent_id + archived`) are fast enough that caching adds complexity without measurable benefit. The `max_messages_in_prompt` limit is pushed into the SQL `LIMIT` clause instead.

### 1.4 `created_at` stored as ISO8601 TEXT

SQLite has no native datetime type. Storing as ISO8601 UTC text (`2024-01-15T10:30:00+00:00`) is consistent with `SQLiteMemoryRepository` and sorts correctly lexicographically. The `id` autoincrement column is the authoritative ordering key for `load()` and `load_full()` — `created_at` is for display and consolidation only.

### 1.5 `Message.timestamp` is optional with `None` default

Adding `timestamp: datetime | None = None` to `Message` is backwards-compatible. Existing callers that construct `Message(role=..., content=...)` continue to work. `append()` mutates the field in-place only when it is `None` — this gives callers the option to pre-set a timestamp (e.g., in tests).

### 1.6 `append` filters non-user/assistant roles

Identical policy to `FileHistoryStore`: SYSTEM, TOOL, and TOOL_RESULT messages are silently dropped. This is a domain rule (the history is a conversation record, not an execution trace) and belongs in the adapter, not in the use case.

---

## 2. Class / Module Design

**Path**: `adapters/outbound/history/sqlite_history_store.py`

```python
"""
SQLiteHistoryStore — historial de conversación persistido en SQLite.

Un registro por mensaje: tabla `history` en data/history.db.
Solo se persisten mensajes user y assistant — nunca tool calls.

Schema:
  history — una fila por mensaje, con soft-delete para archive
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from core.domain.entities.message import Message, Role
from core.domain.errors import HistoryError
from core.ports.outbound.history_port import IHistoryStore
from infrastructure.config import HistoryConfig

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_history_agent ON history(agent_id, archived);
"""


class SQLiteHistoryStore(IHistoryStore):

    def __init__(self, cfg: HistoryConfig) -> None:
        """
        Inicializa el store.

        Args:
            cfg: HistoryConfig con db_path y max_messages_in_prompt.
        """
        self._db_path = cfg.db_path
        self._max_n = cfg.max_messages_in_prompt
        Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        """Abre una conexión con row_factory configurado."""
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        """Crea tabla e índice si no existen. Idempotente."""
        await conn.execute(_CREATE_TABLE)
        await conn.execute(_CREATE_INDEX)
        await conn.commit()

    async def append(self, agent_id: str, message: Message) -> None:
        """
        Inserta el mensaje en la tabla.

        Solo persiste USER y ASSISTANT — ignora el resto silenciosamente.
        Muta message.timestamp si es None, asignando datetime.now(UTC).
        """
        if message.role not in (Role.USER, Role.ASSISTANT):
            return

        now = datetime.now(timezone.utc)
        if message.timestamp is None:
            message.timestamp = now
        ts = message.timestamp.isoformat()

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            await conn.execute(
                "INSERT INTO history (agent_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (agent_id, message.role.value, message.content, ts),
            )
            await conn.commit()

    async def load(self, agent_id: str) -> list[Message]:
        """
        Retorna los mensajes activos del agente.

        Si max_messages_in_prompt > 0: devuelve los últimos N mensajes (DESC LIMIT N, luego invertidos).
        Si max_messages_in_prompt = 0: devuelve todos en orden ASC.
        """
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            if self._max_n > 0:
                rows = await conn.execute_fetchall(
                    "SELECT role, content, created_at FROM history "
                    "WHERE agent_id = ? AND archived = 0 "
                    "ORDER BY id DESC LIMIT ?",
                    (agent_id, self._max_n),
                )
                return [self._row_to_message(r) for r in reversed(rows)]
            else:
                rows = await conn.execute_fetchall(
                    "SELECT role, content, created_at FROM history "
                    "WHERE agent_id = ? AND archived = 0 "
                    "ORDER BY id ASC",
                    (agent_id,),
                )
                return [self._row_to_message(r) for r in rows]

    async def load_full(self, agent_id: str) -> list[Message]:
        """
        Retorna el historial completo del agente en orden cronológico.
        Usar solo para consolidación — ignora max_messages_in_prompt.
        """
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                "SELECT role, content, created_at FROM history "
                "WHERE agent_id = ? AND archived = 0 "
                "ORDER BY id ASC",
                (agent_id,),
            )
        return [self._row_to_message(r) for r in rows]

    async def archive(self, agent_id: str) -> str:
        """
        Marca todos los mensajes activos del agente como archivados.

        Returns:
            Identificador semántico del archivo (no es una ruta de filesystem).
        Raises:
            HistoryError: si no hay mensajes activos para archivar.
        """
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(
                "UPDATE history SET archived = 1 WHERE agent_id = ? AND archived = 0",
                (agent_id,),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                raise HistoryError(f"No hay historial activo para '{agent_id}'")

        archive_ref = f"sqlite:history:{agent_id}"
        logger.info("Historial de '%s' archivado (%d filas)", agent_id, cursor.rowcount)
        return archive_ref

    async def clear(self, agent_id: str) -> None:
        """
        Elimina físicamente TODOS los mensajes del agente (activos y archivados).
        Usar solo tras archive() como parte del flujo de consolidación.
        """
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            await conn.execute("DELETE FROM history WHERE agent_id = ?", (agent_id,))
            await conn.commit()

    def _row_to_message(self, row: aiosqlite.Row) -> Message:
        ts: datetime | None = None
        try:
            ts = datetime.fromisoformat(row["created_at"])
        except (ValueError, TypeError):
            pass
        return Message(
            role=Role(row["role"]),
            content=row["content"],
            timestamp=ts,
        )
```

### `HistoryConfig` (updated in `infrastructure/config.py`)

```python
class HistoryConfig(BaseModel):
    db_path: str = "data/history.db"
    max_messages_in_prompt: int = 0
```

Fields removed: `active_dir`, `archive_dir`. These were filesystem-only concepts.

### `Message` (updated in `core/domain/entities/message.py`)

```python
from datetime import datetime

class Message(BaseModel):
    role: Role
    content: str
    timestamp: datetime | None = None
```

---

## 3. Data Flow

```
User input
    │
    ▼
RunAgentUseCase.execute()
    │
    ├─► history.append(agent_id, Message(role=USER, content=...))
    │       └─► INSERT INTO history (created_at = now UTC)
    │           message.timestamp mutated in place
    │
    ├─► history.load(agent_id)           ← passed to LLM as context
    │       └─► SELECT ... LIMIT N (if max_n > 0) | SELECT ... (all)
    │
    └─► history.append(agent_id, Message(role=ASSISTANT, content=...))
            └─► INSERT INTO history

── Later: ConsolidateMemoryUseCase.execute() ──────────────────────────────

    ├─► history.load_full(agent_id)
    │       └─► SELECT * WHERE archived=0 ORDER BY id ASC
    │           → list[Message] with timestamps
    │
    ├─► format history_text
    │       └─► "{role} [{timestamp}]: {content}"  if timestamp not None
    │           "{role}: {content}"                  otherwise
    │
    ├─► llm.complete(system_prompt=extractor_prompt)
    │       └─► returns JSON: [{content, relevance, tags, timestamp?}, ...]
    │
    ├─► for each fact:
    │       embedder.embed_passage(fact["content"])
    │       MemoryEntry(
    │           created_at = datetime.fromisoformat(fact["timestamp"])
    │                        if "timestamp" in fact else datetime.now(UTC)
    │       )
    │       memory.store(entry)
    │
    ├─► history.archive(agent_id)
    │       └─► UPDATE SET archived=1 WHERE agent_id=? AND archived=0
    │           raises HistoryError if rowcount == 0
    │
    └─► history.clear(agent_id)
            └─► DELETE FROM history WHERE agent_id=?
```

---

## 4. Migration Notes

### Existing `.txt` files

Out of scope for this change. The `FileHistoryStore` is replaced wholesale by wiring `SQLiteHistoryStore` in `container.py`. Existing `.txt` files in `data/history/active/` will be abandoned in place — they will not be read by the new adapter.

**Recommended manual migration** (not automated):
1. For each `data/history/active/{agent_id}.txt`, parse lines and INSERT into the new `history` table with a synthetic `created_at` (e.g., file modification time or `1970-01-01`).
2. Move the `.txt` files to a `data/history/legacy/` backup directory.

Since conversation history is ephemeral by design (it gets consolidated into `MemoryEntry` facts), the practical impact is that any agent with an active `.txt` history at deploy time will start with an empty history. This is acceptable.

### `HistoryConfig` YAML keys

YAML files that currently specify:
```yaml
history:
  active_dir: data/history/active
  archive_dir: data/history/archive
```

Must be updated to:
```yaml
history:
  db_path: data/history.db
```

The old keys are silently ignored by Pydantic (extra fields are `ignore` by default in this codebase). No exception is raised — but the old dirs will no longer be created or used.

---

## 5. Error Handling

| Situation | Error raised | Where |
|-----------|-------------|-------|
| `archive()` called with no active rows | `HistoryError("No hay historial activo para '{agent_id}'")` | `SQLiteHistoryStore.archive()` |
| `aiosqlite` raises `OperationalError` during any DB op | propagated as-is (not wrapped) — caller sees `aiosqlite.OperationalError` | all methods |
| `_row_to_message` receives malformed `created_at` | silently sets `timestamp = None` — non-fatal | `_row_to_message()` |
| `message.role` not in `(USER, ASSISTANT)` | silently returns — no exception | `append()` |

**Note on aiosqlite errors**: The current `FileHistoryStore` wraps `OSError` into `HistoryError`. This design does NOT wrap `aiosqlite` errors because:
1. They already carry descriptive messages.
2. Wrapping would hide the original exception type from upper layers that might want to catch `aiosqlite.OperationalError` specifically (e.g., locked DB).

If a consistent error interface is needed in the future, a thin `try/except aiosqlite.Error` wrapper can be added to each method.

---

## 6. Testing Approach

### Fixtures

```python
# conftest.py
import pytest
import pytest_asyncio

@pytest_asyncio.fixture
async def history_store(tmp_path):
    from infrastructure.config import HistoryConfig
    from adapters.outbound.history.sqlite_history_store import SQLiteHistoryStore
    cfg = HistoryConfig(db_path=str(tmp_path / "test_history.db"))
    return SQLiteHistoryStore(cfg)
```

Each test gets a fresh in-`tmp_path` SQLite file — no shared state, no cleanup needed.

### Isolation strategy

- Use `pytest-asyncio` with `asyncio_mode = "auto"` (already in use by the project).
- One DB file per test function via `tmp_path` fixture — no mocking of aiosqlite.
- Test against the real SQLite engine: this is an integration test of the adapter, not a unit test. Mocking aiosqlite would give no useful coverage.

### Test cases

```
append + load (no limit)
  → append 3 messages, load returns all 3 in ASC order

append + load (with limit)
  → append 5 messages, max_n=2, load returns last 2 in ASC order

load empty agent
  → returns []

load_full ignores max_n
  → append 5 messages, max_n=2, load_full returns all 5

archive marks rows
  → append 2 messages, archive, load returns []
  → load_full still returns [] (archived rows excluded)

archive with no active rows raises HistoryError
  → archive on empty agent_id raises HistoryError

clear removes all rows
  → append 2, archive (soft-delete), clear, DB has 0 rows for agent

timestamp mutation
  → append message with timestamp=None, message.timestamp is set after call

timestamp preserved
  → append message with explicit timestamp, stored value matches

multi-agent isolation
  → append to agent_a and agent_b, load(agent_a) does not include agent_b rows

consolidation flow (integration)
  → append messages, load_full, archive, clear — mimics ConsolidateMemoryUseCase
```

### What NOT to test here

- `ConsolidateMemoryUseCase` timestamp formatting — that belongs in the use case's own test.
- `HistoryConfig` parsing — covered by config tests.
- Schema migration — no migration code exists; test only the current schema.
