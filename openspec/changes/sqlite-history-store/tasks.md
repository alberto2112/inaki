# Tasks: sqlite-history-store

**Change:** Replace `FileHistoryStore` with `SQLiteHistoryStore`  
**Total tasks:** 16  
**Date:** 2026-04-09

---

## Phase 1: Domain — Message entity

- [x] T1: Add `timestamp: datetime | None = None` field to `Message` entity — `core/domain/entities/message.py` | **S**
  - Non-breaking: all existing `Message(role=..., content=...)` call-sites remain valid.
  - Add `from datetime import datetime` import.

---

## Phase 2: Port — IHistoryStore docstring

- [x] T2: Update `IHistoryStore.archive` docstring — `core/ports/outbound/history_port.py` | **S**
  - Replace "Mueve el historial activo a /archive. Retorna la ruta del archivo." with "Marca el historial activo como archivado. Retorna una cadena de confirmación (no es una ruta de filesystem)."
  - Also update `load_full` docstring: remove "leyendo desde disco" — no longer file-based.
  - No signature changes.

---

## Phase 3: Config — HistoryConfig migration

- [x] T3: Update `HistoryConfig` in `infrastructure/config.py` — **S**
  - Remove `active_dir: str` and `archive_dir: str` fields.
  - Add `db_path: str = "data/history.db"`.
  - Result: `HistoryConfig` has exactly `db_path` and `max_messages_in_prompt`.

- [x] T4: Update `config/global.yaml` — **S** (depends on T3)
  - Remove `history.active_dir` and `history.archive_dir` keys.
  - Add `history.db_path: data/history.db`.
  - Keep `max_messages_in_prompt: 21`.

- [x] T5: Verify agent YAMLs have no legacy history fields — `config/agents/*.yaml` — **S** (depends on T3)
  - Inspect `general.yaml` and `dev.yaml`: neither currently defines a `history:` section, so no changes needed.
  - If any agent YAML does define `active_dir` / `archive_dir`, remove those keys.

---

## Phase 4: New Adapter — SQLiteHistoryStore

- [x] T6: Create `adapters/outbound/history/sqlite_history_store.py` — **L** (depends on T1, T2, T3)
  - Full implementation per design doc section 2:
    - `__init__(cfg: HistoryConfig)`: store `db_path`, `max_n`; `Path(db_path).parent.mkdir(parents=True, exist_ok=True)`.
    - `_conn()`: `asynccontextmanager`; opens `aiosqlite.connect(self._db_path)`, sets `conn.row_factory = aiosqlite.Row`.
    - `_ensure_schema(conn)`: executes `CREATE TABLE IF NOT EXISTS history (...)` and `CREATE INDEX IF NOT EXISTS idx_history_agent ON history(agent_id, archived)`; commits.
    - `append(agent_id, message)`: skip non-USER/ASSISTANT roles; derive `created_at` from `message.timestamp` if set, else `datetime.now(timezone.utc)`; mutate `message.timestamp` if `None`; `INSERT INTO history`.
    - `load(agent_id)`: if `max_n > 0` → `ORDER BY id DESC LIMIT ?` then reverse; else `ORDER BY id ASC`; filter `archived = 0`; reconstruct via `_row_to_message`.
    - `load_full(agent_id)`: `ORDER BY id ASC`, no LIMIT, `archived = 0`.
    - `archive(agent_id)`: `UPDATE SET archived=1 WHERE agent_id=? AND archived=0`; raise `HistoryError` if `rowcount == 0`; return `f"sqlite:history:{agent_id}"`.
    - `clear(agent_id)`: `DELETE FROM history WHERE agent_id = ?`.
    - `_row_to_message(row)`: parse `created_at` via `datetime.fromisoformat`; silence parse errors (set `ts = None`); return `Message(role=Role(row["role"]), content=row["content"], timestamp=ts)`.
  - Schema SQL constants: `_CREATE_TABLE`, `_CREATE_INDEX` as module-level strings.
  - `aiosqlite` errors are NOT wrapped — propagate as-is per design decision 5.

---

## Phase 5: Delete obsolete adapter

- [x] T7: Delete `adapters/outbound/history/file_history_store.py` — **S** (depends on T6)
  - Remove the file. No code should import it after T8 is done.

---

## Phase 6: Container wiring

- [x] T8: Update `infrastructure/container.py` — **S** (depends on T6, T7)
  - Replace `from adapters.outbound.history.file_history_store import FileHistoryStore` with `from adapters.outbound.history.sqlite_history_store import SQLiteHistoryStore`.
  - Replace `self._history = FileHistoryStore(cfg.history)` with `self._history = SQLiteHistoryStore(cfg.history)`.

---

## Phase 7: Consolidation use case

- [x] T9: Update history formatting in `consolidate_memory.py` — **S** (depends on T1)
  - Replace `"\n".join(f"{m.role.value}: {m.content}" ...)` with conditional format:
    ```python
    def _fmt(m):
        if m.timestamp is not None:
            ts = m.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
            return f"{m.role.value} [{ts}]: {m.content}"
        return f"{m.role.value}: {m.content}"
    history_text = "\n".join(_fmt(m) for m in messages if m.role in (Role.USER, Role.ASSISTANT))
    ```

- [x] T10: Update `_EXTRACTOR_PROMPT_TEMPLATE` in `consolidate_memory.py` — **S** (depends on T9)
  - Add `"timestamp"` field to the example JSON schema in the prompt:
    ```json
    {
      "content": "descripción clara del hecho o preferencia",
      "relevance": 0.0-1.0,
      "tags": ["tag1", "tag2"],
      "timestamp": "2026-04-09T15:30:00Z"
    }
    ```
  - Add note: `"timestamp es opcional; si no aplica, omitirlo."`.

- [x] T11: Set `MemoryEntry.created_at` from LLM timestamp in `consolidate_memory.py` — **M** (depends on T10)
  - In the fact-persistence loop, parse `fact.get("timestamp")`:
    ```python
    raw_ts = fact.get("timestamp")
    created_at = None
    if raw_ts:
        try:
            created_at = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    entry = MemoryEntry(
        ...,
        created_at=created_at or datetime.now(timezone.utc),
    )
    ```
  - Add `from datetime import datetime, timezone` if not present.

- [x] T12: Update `ConsolidationResult` dataclass and return message in `consolidate_memory.py` — **S** (depends on T8, T11)
  - Rename `archive_path: str` → `archive_ref: str` in `ConsolidationResult` (or remove the dataclass entirely if unused).
  - Update return string from `f"✓ {stored} recuerdo(s) extraído(s). Historial archivado en {archive_path}"` to `f"✓ {stored} recuerdo(s) extraído(s). Historial archivado ({archive_ref})."`.
  - Update log message accordingly.

---

## Phase 8: Tests — new adapter

- [x] T13: Create `tests/unit/adapters/test_sqlite_history_store.py` — **L** (depends on T6)
  - Fixture: `history_store(tmp_path)` — `HistoryConfig(db_path=str(tmp_path / "test.db"))`, `max_messages_in_prompt=0`.
  - Fixture: `history_store_limited(tmp_path)` — same but `max_messages_in_prompt=3`.
  - Test cases covering all spec scenarios SC-01 through SC-14:
    - `test_append_user_with_timestamp` (SC-01): appended row has correct `created_at`.
    - `test_append_without_timestamp_assigns_utc_now` (SC-02): `message.timestamp` mutated; `created_at` is recent UTC.
    - `test_append_ignores_system_and_tool_roles` (SC-03): `load` returns `[]` after SYSTEM/TOOL appends.
    - `test_load_windowed_returns_last_n_asc` (SC-04): 5 messages, limit=3, returns last 3 in ASC order.
    - `test_load_no_limit_returns_all` (SC-05): `max_n=0`, 5 messages → all 5 returned.
    - `test_load_unknown_agent_returns_empty` (SC-06).
    - `test_load_excludes_archived_rows` (SC-07): archive then append 2 new → load returns only 2.
    - `test_load_full_ignores_max_n` (SC-08): `max_n=3`, 10 messages, `load_full` returns all 10.
    - `test_archive_soft_deletes_and_returns_string` (SC-09): `archive` returns non-empty string, `load` → `[]`.
    - `test_archive_raises_when_no_active_rows` (SC-10).
    - `test_archive_raises_when_already_archived` (SC-11).
    - `test_clear_removes_all_rows` (SC-12): 3 archived + 2 active → `clear` → `load` and `load_full` both `[]`.
    - `test_clear_unknown_agent_no_raise` (SC-13).
    - `test_multi_agent_isolation` (SC-14): two agents, operations on one don't affect the other.
  - All tests use `pytest-asyncio` (already in project); `asyncio_mode = "auto"` assumed active.

---

## Phase 9: Tests — delete obsolete

- [x] T14: Delete `tests/unit/adapters/test_file_history_store.py` — **S** (depends on T7)

---

## Phase 10: Tests — consolidation use case

- [x] T15: Update `tests/unit/use_cases/test_consolidate_memory.py` — **M** (depends on T9, T10, T11, T12)
  - Add test scenarios SC-15 through SC-18:
    - `test_consolidation_formats_message_with_timestamp` (SC-15): history_text includes `"user [2026-04-09T15:30:00Z]: text"`.
    - `test_consolidation_formats_message_without_timestamp` (SC-16): history_text includes `"user: text"`.
    - `test_consolidation_sets_created_at_from_llm_timestamp` (SC-17): `MemoryEntry.created_at` matches LLM-returned timestamp.
    - `test_consolidation_falls_back_to_now_when_no_timestamp` (SC-18): `MemoryEntry.created_at` is approximately `datetime.now(UTC)`.
  - Verify return string no longer contains "archivado en" + file path.

---

## Phase 11: Cleanup check

- [x] T16: Verify no remaining references to `FileHistoryStore`, `active_dir`, or `archive_dir` in codebase — **S** (depends on all above)
  - Run `rg "FileHistoryStore|active_dir|archive_dir" --type py` and in YAML files.
  - Confirm clean result.

---

## Dependency Summary

```
T1 (Message.timestamp)
  └─► T6 (SQLiteHistoryStore)
        ├─► T7 (delete FileHistoryStore)
        │     └─► T8 (container wiring)
        │           └─► T12 (consolidation return msg)
        └─► T13 (test_sqlite_history_store)
              └─► T14 (delete test_file_history_store)

T2 (port docstring)
  └─► T6

T3 (HistoryConfig)
  ├─► T4 (global.yaml)
  ├─► T5 (agent YAMLs check)
  └─► T6

T9 (consolidation history format)
  └─► T10 (extractor prompt)
        └─► T11 (MemoryEntry.created_at)
              └─► T12
                    └─► T15 (consolidation tests)

T16 (cleanup check) — depends on all
```

## Complexity Summary

| Task | Complexity | Phase |
|------|-----------|-------|
| T1  | S | Domain |
| T2  | S | Port |
| T3  | S | Config |
| T4  | S | Config |
| T5  | S | Config |
| T6  | L | Adapter |
| T7  | S | Cleanup |
| T8  | S | DI |
| T9  | S | Use case |
| T10 | S | Use case |
| T11 | M | Use case |
| T12 | S | Use case |
| T13 | L | Tests |
| T14 | S | Tests |
| T15 | M | Tests |
| T16 | S | Verify |

**Total: 16 tasks** — 2L, 2M, 12S
