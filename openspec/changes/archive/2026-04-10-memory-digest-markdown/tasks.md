# Tasks: memory-digest-markdown

**Change:** Replace per-turn `memory.search` vector lookup with a consolidate-time markdown digest  
**Total tasks:** 22  
**Date:** 2026-04-10

---

## Phase 1 — Config foundation

- [x] 1.1 Add `digest_size: int = 14` and `digest_path: Path = Path("~/.inaki/mem/last_memories.md")` to `MemoryConfig` in `infrastructure/config.py`. Add pydantic `field_validator("digest_path", mode="before")` that returns `Path(v).expanduser()`. → FR-08, AC-06
  - Import `from pathlib import Path` and `from pydantic import field_validator` if not present.

- [x] 1.2 Add the two fields with inline comments (Spanish, matching existing comment style) to the `memory:` block of `config/global.example.yaml` (after `default_top_k`). Include the Pi 5 production path example and the user-data-separation rationale. → FR-08
  - See design section 4.7 for exact YAML block.

- [x] 1.3 Write `tests/unit/infrastructure/test_config.py` covering: loading with explicit values, defaults, and `~` expansion (SC-15, SC-16). → AC-06 (actual path: `tests/unit/infrastructure/test_config.py`)
  - Verify if this file exists first; create if absent.
  - Tests: `cfg.digest_size == 14` when default; `cfg.digest_path.is_absolute()` and no `~` in path after expansion.

---

## Phase 2 — Domain value object

- [x] 2.1 In `core/domain/value_objects/agent_context.py`: remove `memories: list[MemoryEntry] = []`, remove the `MemoryEntry` import, add `memory_digest: str = ""`, and update `build_system_prompt` to append `"\n" + self.memory_digest` only when `self.memory_digest.strip()` is truthy. The digest already contains its own `# Recuerdos sobre el usuario` header — do NOT wrap it. → FR-03, AC-08

- [x] 2.2 Write/update `tests/unit/domain/test_agent_context.py` (create if absent): empty-digest renders `base_prompt` unchanged; non-empty digest is concatenated verbatim. → NFR-03, NFR-04, AC-08
  - SC-05 coverage: `AgentContext(memory_digest="# foo\n- bar").build_system_prompt(base)` contains `"# foo\n- bar"`.

---

## Phase 3 — Consolidate writes digest

- [x] 3.1 In `core/use_cases/consolidate_memory.py`: add `memory_config: MemoryConfig` to `__init__` as the final parameter, store as `self._memory_cfg`. Import `MemoryConfig` from `infrastructure/config.py`. → FR-06

- [x] 3.2 In `core/use_cases/consolidate_memory.py`: add `_render_digest(self, memories: list[MemoryEntry]) -> str` private method per design section 4.2 pseudocode. → FR-07, SC-12, SC-13, SC-14
  - Format: `- [YYYY-MM-DD] {content} ({tag1}, {tag2})` with parenthetical omitted when tags are empty.
  - `created_at=None` falls back to `datetime.now(timezone.utc)`.

- [x] 3.3 In `core/use_cases/consolidate_memory.py`: add `async _write_digest(self) -> None` private method. Call `memory.get_recent(self._memory_cfg.digest_size)`, render, `mkdir(parents=True, exist_ok=True)`, `write_text`. Wrap ALL body in `except Exception` logged at ERROR — never raises. → FR-02, FR-09, NFR-02, SC-19

- [x] 3.4 In `core/use_cases/consolidate_memory.py`: call `await self._write_digest()` AFTER the `for fact in facts` loop and BEFORE `history.archive` (current line 151). → design decision 2.3, FR-05

- [x] 3.5 In `infrastructure/container.py:66-72`: add `memory_config=cfg.memory,` as a final kwarg when constructing `ConsolidateMemoryUseCase`. → design Q3 resolution

- [x] 3.6 Update `tests/unit/use_cases/test_consolidate_memory.py`: add tests for:
  - (a) Digest file written with correct format: starts with `# Recuerdos sobre el usuario`, has timestamp comment, bullet lines match `- [YYYY-MM-DD] ...` → SC-03, SC-12, SC-13, SC-14, AC-04
  - (b) `get_recent` called with `limit=cfg.memory.digest_size` → SC-10, SC-11, AC-05
  - (c) `archive` + `clear` still invoked in order → SC-09, FR-05, AC-04
  - (d) `_write_digest` IOError caught, archive/clear still run → FR-09, NFR-03
  - (e) Parent directory auto-created → SC-19, NFR-02

---

## Phase 4 — Run-agent hot path

- [x] 4.1 In `core/use_cases/run_agent.py`: delete line 73 `top_k = ...` and lines 79-80 (unconditional `embed_query` + `memory.search` call). Delete the `memories` local variable. → FR-04, AC-10

- [x] 4.2 In `core/use_cases/run_agent.py`: add `_read_digest(self) -> str` private method per design section 4.1. Catch `FileNotFoundError` (DEBUG log) and `OSError` (WARNING log), return `""` on any failure. → FR-03, FR-10, NFR-01

- [x] 4.3 In `core/use_cases/run_agent.py`: reorder `execute()` per design section 4.1 data flow: `history.load` → `_read_digest` → `list_all`/`get_schemas` → compute flags → conditional `embed_query` block → `AgentContext(memory_digest=digest_text, ...)`. → FR-01, FR-03

- [x] 4.4 Update `InspectResult` (lines 38-48 of `run_agent.py`): replace `memories: list[MemoryEntry]` with `memory_digest: str`. Remove the `MemoryEntry` import from `run_agent.py` if no longer referenced. → FR-09, AC-07, SC-17

- [x] 4.5 Mirror the `execute()` reorder in `inspect()`: call `_read_digest`, remove `memory.search`, pass `memory_digest` to `AgentContext`, return `memory_digest` in `InspectResult`. → FR-04, SC-08, SC-17

- [x] 4.6 Update `adapters/inbound/cli/cli_runner.py:111-113`: replace `print(f"📍 Memorias recuperadas ({len(result.memories)}):")` and the `for m in result.memories` loop with a block that prints the digest string (e.g. `print("📍 Digest de memoria:")` then `print(result.memory_digest or "(sin digest)")`). Keep existing heading style/emoji. → design Q2 resolution, FR-09

- [x] 4.7 Update `tests/unit/use_cases/test_run_agent_basic.py`: add tests for:
  - (a) `embed_query` zero-calls when both RAG flags false → SC-01, FR-01, AC-01
  - (b) `embed_query` ≥1 call when either flag true → SC-02, AC-01
  - (c) `memory.search` not called in `execute` or `inspect` → SC-07, SC-08, FR-04, AC-02
  - (d) Digest-present → system prompt contains digest content → SC-05, AC-03
  - (e) Digest-absent → no crash, system prompt contains base_prompt → SC-06, FR-10, SC-18, AC-03
  - (f) `_read_digest` returns `""` on `PermissionError` → NFR-03, FR-03
  - (g) `InspectResult.memory_digest` matches what `execute` injects → SC-17, NFR-05

---

## Phase 5 — Verification and cleanup

- [x] 5.1 Run the full pytest suite: `pytest -q`. Confirm all pre-existing tests pass AND all new tests pass. → AC-09, NFR-04

- [x] 5.2 `rg 'memory\.search' core/use_cases/run_agent.py` returns no matches. → AC-10

- [x] 5.3 `rg 'list_latest' core/ adapters/ infrastructure/ tests/` returns no matches anywhere — must not have leaked. → design Q4 resolution

- [x] 5.4 Confirm `SQLiteMemoryRepository.search` method is still defined and its existing tests pass. → NFR-05, AC-09

---

## Dependency Summary

```
Phase 1 (Config) ──────────────────────┐
                                        ↓
Phase 2 (AgentContext) ──┐        Phase 3 (Consolidate) → depends on Phase 1
                          ↓
                    Phase 4 (RunAgent) → depends on Phase 2
                          ↓
                    Phase 5 (Verify)  → depends on Phase 3 + Phase 4
```

## Complexity Summary

| Task | Complexity | Phase |
|------|-----------|-------|
| 1.1 | S | Config |
| 1.2 | S | Config |
| 1.3 | S | Config tests |
| 2.1 | S | Domain |
| 2.2 | S | Domain tests |
| 3.1 | S | Consolidate |
| 3.2 | S | Consolidate |
| 3.3 | M | Consolidate |
| 3.4 | S | Consolidate |
| 3.5 | S | DI wiring |
| 3.6 | M | Consolidate tests |
| 4.1 | S | RunAgent |
| 4.2 | S | RunAgent |
| 4.3 | M | RunAgent |
| 4.4 | S | RunAgent |
| 4.5 | S | RunAgent |
| 4.6 | S | CLI |
| 4.7 | M | RunAgent tests |
| 5.1 | S | Verify |
| 5.2 | S | Verify |
| 5.3 | S | Verify |
| 5.4 | S | Verify |

**Total: 22 tasks** — 4M, 18S
