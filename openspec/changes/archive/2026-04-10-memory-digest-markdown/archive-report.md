# Archive Report: memory-digest-markdown

**Change**: memory-digest-markdown
**Project**: inaki
**Archived**: 2026-04-10
**Mode**: hybrid (openspec files + engram)
**Final Status**: PASS — all warnings addressed or documented (Warning 4 fixed at archive time; Warning 2 corrected in docs; Warning 3 deferred with rationale)

---

## Summary

This change replaced the per-turn `memory.search` vector lookup in `RunAgentUseCase` with a stable markdown digest regenerated at consolidate time. The core result: `embed_query` is no longer called unconditionally on every agent turn — it is now gated by `skills_rag_active or tools_rag_active`. When both flags are false (the common case with few skills/tools), every agent turn performs zero embedding calls.

The digest is a human-readable markdown file written to `~/.inaki/mem/last_memories.md` at the end of each `/consolidate` run. It captures the N most recent long-term memories (`created_at DESC`, default N=14) and is injected verbatim into the system prompt by `AgentContext.build_system_prompt`. The file lives outside the project tree — this is the first concrete application of the user-data separation architectural principle for the Inaki project.

Implementation was clean and complete across two apply batches. All 10 FRs and 5 NFRs were satisfied. At archive time, one code fix was applied (Warning 4: `_read_digest` now also catches `UnicodeDecodeError`), bringing NFR-03 to full compliance. Documentation was corrected to use accurate test paths (no stale `core/` segment). The final test count is 113 passed, 0 failed.

---

## Artifact Traceability

| Artifact | Engram Topic Key | File |
|----------|------------------|------|
| Proposal | sdd/memory-digest-markdown/proposal | proposal.md |
| Spec | sdd/memory-digest-markdown/spec | spec.md |
| Design | sdd/memory-digest-markdown/design | design.md |
| Tasks | sdd/memory-digest-markdown/tasks | tasks.md |
| Apply Progress | sdd/memory-digest-markdown/apply-progress | apply-progress.md |
| Verify Report | sdd/memory-digest-markdown/verify-report | verify-report.md |
| Archive Report | sdd/memory-digest-markdown/archive-report | archive-report.md |

---

## Final Test Result

**113 passed, 0 failed** (1 pre-existing asyncio deprecation warning, unrelated)

---

## Code Changes Shipped (total across batches 1+2+archive)

- **`infrastructure/config.py`**: Added `digest_size: int = 14` and `digest_path: Path` fields to `MemoryConfig`. Added `field_validator("digest_path", mode="before")` for explicit values. Added `model_post_init` to expand the class-level `~` default (pydantic v2 edge case). Added `_render_default_global_yaml()` fix for `yaml.safe_dump` Path serialization (`str(mem["digest_path"])`).
- **`core/domain/value_objects/agent_context.py`**: Removed `memories: list[MemoryEntry]` field and import. Added `memory_digest: str = ""`. Updated `build_system_prompt` to append digest verbatim when non-empty.
- **`core/use_cases/run_agent.py`**: Added `_read_digest()` private method (catches `FileNotFoundError`, `UnicodeDecodeError` [archive fix], `OSError`). Rewrote `execute()` and `inspect()` to: call `_read_digest` at top, compute RAG flags before `embed_query`, gate `embed_query` inside `if skills_rag_active or tools_rag_active`. Updated `InspectResult` to replace `memories: list[MemoryEntry]` with `memory_digest: str`.
- **`core/use_cases/consolidate_memory.py`**: Added `memory_config: MemoryConfig` constructor param. Added `_render_digest()` and `_write_digest()` private methods. Called `_write_digest()` after the facts loop, before `history.archive`.
- **`infrastructure/container.py`**: Added `memory_config=cfg.memory` kwarg to `ConsolidateMemoryUseCase` construction.
- **`adapters/inbound/cli/cli_runner.py`**: Replaced `memories` loop print with digest string print.
- **`config/global.example.yaml`**: Added `digest_size` and `digest_path` fields under `memory:` with Spanish inline comments.
- **`tests/unit/infrastructure/test_config.py`**: New file — 8 tests covering MemoryConfig defaults, explicit values, `~` expansion, `is_absolute()`, Path type.
- **`tests/unit/domain/test_agent_context.py`**: New file — 7 tests covering empty/whitespace/non-empty digest behavior in `build_system_prompt`.
- **`tests/unit/use_cases/test_consolidate_memory.py`**: Extended with 5 new tests (digest format, get_recent call_count, archive+clear order, IOError resilience, parent dir creation).
- **`tests/unit/use_cases/test_run_agent_basic.py`**: Added 8 new tests (embed_query zero-calls, embed_query active, memory.search not called, digest-present/absent behavior, UnicodeDecodeError swallow [archive fix], InspectResult field check). Updated 2 stale tests to reflect new conditional-RAG contract.

---

## Corrections Applied at Archive Time

- **Warning 4 (NFR-03 encoding gap) — FIXED**: `_read_digest` now catches `UnicodeDecodeError` in addition to `FileNotFoundError` and `OSError`. A malformed-encoding digest file no longer crashes the agent turn. Added test `test_read_digest_returns_empty_on_unicode_decode_error` in `tests/unit/use_cases/test_run_agent_basic.py`.
- **Warning 2 (test path docs) — FIXED**: spec.md, design.md, tasks.md updated to use `tests/unit/...` without the stale `core/` segment. AC-01 through AC-08, Test Matrix in spec.md, and Testing Strategy table in design.md all corrected. Test file name corrected from `test_run_agent.py` to `test_run_agent_basic.py`.
- **Design section 4.6 (model_post_init requirement) — DOCUMENTED**: design.md section 4.6 now accurately reflects both `field_validator` and `model_post_init`, with a note explaining the pydantic v2 edge case that batch 1 discovered and sdd-verify confirmed (INV-7).

---

## Follow-ups Deferred to Future Work

- **Warning 3 — `yaml.safe_dump` expands `~` in generated `global.yaml`**: `_render_default_global_yaml` currently casts `Path` to `str`, which serializes the fully-expanded path. This leaks the developer's home directory into any generated example YAML. Cosmetic but worth fixing. Recommended: add a pydantic `@field_serializer("digest_path")` that returns a string preserving `~` for the home directory, or pre-process `mem["digest_path"]` in `_render_default_global_yaml` to replace the home prefix with `~`. Not blocking — the runtime path is always the expanded form, which is correct.
- **Suggestion 1 from verify**: add `assert not nonexistent.exists()` to `test_digest_absent_no_exception` for NFR-01 explicit coverage. Nice-to-have; current code inspection already proves the property.
- **Suggestion 2 from verify**: add a named SC-11 test with `digest_size=20`. Existing test covers the invariant with `digest_size=3`; naming nitpick only.

---

## Key Decisions Preserved (for future reference)

1. `SQLiteMemoryRepository.search` and `IMemoryRepository.search` are **deliberately preserved** as dormant code. They will be reactivated by a future LLM tool that gives the agent on-demand access to old memories. Do not delete them in a future cleanup pass.
2. The digest write happens BEFORE `history.archive`/`clear` in `ConsolidateMemoryUseCase.execute`. This is the fail-safer order: a failed archive still leaves a correct digest; the inverse would leave a stale digest after a successful archive.
3. `memory_digest` is a plain `str` in `AgentContext`, concatenated verbatim without heading wrapping. The digest file owns its own header. Do not wrap it in `AgentContext.build_system_prompt`.
4. `memory.digest_path` lives under `~/.inaki/mem/`, NOT under the project's `data/` directory. This is the first concrete application of the "user-data separation" architectural principle. Future user-owned paths (configs, custom skills, etc.) should also migrate to `~/.inaki/`.
5. Pydantic v2 `field_validator(mode="before")` does NOT run on class-level defaults. For fields whose DEFAULT needs normalization (e.g. `~` expansion), `model_post_init` is required in addition.

---

## Where to Find This Change After Archive

- **Files**: `openspec/changes/archive/2026-04-10-memory-digest-markdown/` (moved from `openspec/changes/memory-digest-markdown/`)
- **Global specs**: `openspec/specs/memory-digest/spec.md` — contractual requirements (FR-01 through FR-10, NFR-01 through NFR-05) persisted as a permanent reference.
- **Engram topic keys**: all `sdd/memory-digest-markdown/*` keys are preserved indefinitely and searchable.
