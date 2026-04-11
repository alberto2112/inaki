# Apply Progress: memory-digest-markdown

**Batches:** 1 + 2 of 2 (all phases complete)
**Date:** 2026-04-10
**Status:** ALL TASKS DONE — ready for archive

## Phase 1 — Config foundation
- [x] 1.1 MemoryConfig fields added — `digest_size: int = 14` and `digest_path: Path` with `field_validator` + `model_post_init` for default expansion. Both validators working: explicit args go through `field_validator`, class default expanded via `model_post_init`.
- [x] 1.2 global.example.yaml documented — Two fields with Spanish inline comments, Pi 5 path example, user-data-separation rationale, added after `default_top_k` block.
- [x] 1.3 test_config.py created — 8 tests, all pass. Covers defaults, explicit values, `~` expansion at load time, `is_absolute()` assertion, Path type assertion.

## Phase 2 — Domain value object
- [x] 2.1 AgentContext refactored — Removed `memories: list[MemoryEntry]` field and `MemoryEntry` import. Added `memory_digest: str = ""`. `build_system_prompt` appends digest verbatim with `"\n" + self.memory_digest` guard when `strip()` is truthy.
- [x] 2.2 test_agent_context.py created — 7 tests, all pass. Covers: empty digest → exact base_prompt, whitespace-only → empty, non-empty verbatim, no double header, base prompt first, no stray newlines.

## Phase 3 — Consolidate writes digest
- [x] 3.1 ConsolidateMemoryUseCase.__init__ updated — `memory_config: MemoryConfig` added as final param, stored as `self._memory_cfg`. Import added.
- [x] 3.2 `_render_digest` added — Formats header + ISO comment + bullet lines. Tag suffix `(tag1, tag2)` present only when tags non-empty. `created_at` fallback to `datetime.now(timezone.utc)`.
- [x] 3.3 `_write_digest` added — Calls `get_recent(digest_size)`, renders, mkdir parents, write_text. Entire body wrapped in `except Exception` — never raises.
- [x] 3.4 `_write_digest` called in `execute()` — Placed after `for fact in facts` loop, before `history.archive` block.
- [x] 3.5 container.py wired — `memory_config=cfg.memory,` added to `ConsolidateMemoryUseCase(...)` construction.
- [x] 3.6 test_consolidate_memory.py extended — Existing `use_case` fixture updated to include `memory_config`. 5 new tests added (a–e): format check, get_recent call_count, archive+clear order, IOError resilience, parent dir creation. All 16 tests pass.

## Phase 4 — Run-agent hot path
- [x] 4.1 Delete unconditional embed_query + memory.search from run_agent.py — combined with 4.3 for a clean single rewrite.
- [x] 4.2 `_read_digest(self) -> str` added — catches FileNotFoundError (DEBUG) and OSError (WARNING), returns "" on any failure.
- [x] 4.3 execute() reordered per design section 4.1 — history.load → _read_digest → list_all/get_schemas → compute flags → conditional embed_query block → AgentContext(memory_digest=digest_text). embed_query now ONLY called when skills_rag_active or tools_rag_active.
- [x] 4.4 InspectResult updated — `memories: list[MemoryEntry]` replaced with `memory_digest: str`. MemoryEntry import removed from run_agent.py.
- [x] 4.5 inspect() mirrored — _read_digest called, memory.search removed, memory_digest passed to AgentContext, memory_digest returned in InspectResult.
- [x] 4.6 cli_runner.py updated — Replaced memories loop with `print("📍 Digest de memoria:")` + `print(result.memory_digest or "   (sin digest)")`.
- [x] 4.7 8 new tests added to test_run_agent_basic.py (corrected path — no `core/` segment):
  - (a) embed_query zero-calls when both RAG flags false → SC-01, FR-01, AC-01
  - (b) embed_query called when skills RAG active → SC-02, AC-01
  - (c) memory.search not called in execute → SC-07, FR-04, AC-02
  - (d) memory.search not called in inspect → SC-08, FR-04, AC-02
  - (e) digest-present injected into system prompt → SC-05, AC-03
  - (f) digest-absent no exception, no placeholder in prompt → SC-06, FR-10, SC-18, AC-03
  - (g) _read_digest swallows PermissionError → NFR-03, FR-03
  - (h) InspectResult has memory_digest not memories → SC-17, AC-07

  Also updated 2 existing stale tests that contradicted the new conditional RAG design:
  - `test_execute_calls_embed_query` → `test_execute_does_not_call_embed_query_when_rag_inactive`
  - `test_execute_searches_memory_with_embedding` → `test_execute_does_not_call_memory_search`

## Phase 5 — Verification and cleanup
- [x] 5.1 Full pytest suite: 112 passed, 0 failed (after fixing yaml serialization bug — see notes)
- [x] 5.2 `memory.search` in run_agent.py: 0 matches → AC-10 ✓
- [x] 5.3 `list_latest` anywhere in code: 0 matches (only in openspec docs) → design Q4 resolution ✓
- [x] 5.4 SQLiteMemoryRepository.search defined at line 94. IMemoryRepository.search defined at line 11. ✓

## Test results this batch
- Phase 4 existing tests (pre-additions): `pytest tests/unit/use_cases/test_run_agent_basic.py` → 5 passed (after updating 2 stale tests)
- Phase 4 new tests (post-additions): `pytest tests/unit/use_cases/test_run_agent_basic.py` → 13 passed
- Full suite (task 5.1): 112 passed, 0 failed, 1 warning (asyncio deprecation, pre-existing)

## Notes for archive

1. **Test path deviation**: All artifacts reference `tests/unit/core/use_cases/` and `tests/unit/core/domain/` — these paths do NOT exist. Actual layout has no `core/` segment: `tests/unit/use_cases/`, `tests/unit/domain/`, `tests/unit/infrastructure/`. Consider updating proposal/spec/design/tasks during archive.

2. **`model_post_init` deviation**: Pydantic v2 `field_validator(mode="before")` does NOT run on class-level default values. Added `model_post_init` to expand the `~` default. Intentional deviation from design pseudocode — end behavior identical. Consider updating design section 4.6 during archive.

3. **Tasks 4.1 + 4.3 combined**: Applied as a single file rewrite for a cleaner diff. Both tasks are complete.

4. **yaml.safe_dump Path serialization bug** (batch 2 discovery and fix): `_render_default_global_yaml()` in `infrastructure/config.py` passed `MemoryConfig().model_dump()` to `yaml.safe_dump`, but `digest_path` is now a `Path` object — `yaml.safe_dump` raises `RepresenterError`. Fixed by `mem["digest_path"] = str(mem["digest_path"])` before serializing. This caused 6 failures in `test_ensure_user_config.py` that are now fixed.

5. **Two stale existing tests updated**: These were pre-existing tests that assumed unconditional `embed_query` and `memory.search` — both of which are now gone. Updated to assert the new correct behavior.

## Archive-time corrections

- [x] Warning 4 (from verify-report): `_read_digest` now catches `UnicodeDecodeError` in addition to `FileNotFoundError` and `OSError`. NFR-03 fully satisfied.
- [x] Warning 2 (from verify-report): test paths in spec/design/tasks corrected from `tests/unit/core/...` to `tests/unit/...`.
- [x] Design section 4.6 updated to document the `model_post_init` requirement for pydantic v2 default expansion.
- [ ] Warning 3 (from verify-report): `yaml.safe_dump` outputs an expanded path in generated `global.yaml`. Deferred as follow-up — see archive-report.md.
