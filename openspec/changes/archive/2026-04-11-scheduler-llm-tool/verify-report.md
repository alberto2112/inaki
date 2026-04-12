# Verification Report: scheduler-llm-tool

**Change**: scheduler-llm-tool
**Branch**: feat/scheduler-llm-tool
**Date**: 2026-04-11
**Mode**: Standard (no Strict TDD)
**Artifact Store**: hybrid

---

## Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 14 |
| Tasks complete | 14 |
| Tasks incomplete | 0 |

All 14 tasks marked complete in apply-progress.md. Verified against source files — all declared files exist and contain the expected implementations.

---

## Build & Tests Execution

**Build / Type check**: ⚠️ Pre-existing errors only (no regressions from this change)

```
mypy found 25 errors in 12 files.
Files relevant to THIS change:
  adapters/outbound/tools/scheduler_tool.py:262 — "ModelMetaclass" has no attribute "model_validate"
  adapters/outbound/tools/scheduler_tool.py:447 — "ModelMetaclass" has no attribute "model_validate"

All other errors (write_file_tool.py, patch_file_tool.py, read_file_tool.py,
web_search_tool.py, sqlite_scheduler_repo.py, container.py, etc.) are
pre-existing and not introduced by this change.
```

Two new mypy errors on `scheduler_tool.py` lines 262 and 447 are false positives: `model_validate` is called on a concrete Pydantic class (`ChannelSendPayload`, `AgentSendPayload`, `ShellExecPayload`) resolved at runtime via the `_TRIGGER_PAYLOAD_MODELS` dict, but mypy sees the dict value type as `ModelMetaclass` (the metaclass), not the concrete class. The code is correct and all tests pass — this is a mypy typing limitation with Pydantic model class dicts, not a real bug.

**Tests**: ✅ 81 passed / ❌ 0 failed / ⚠️ 0 skipped

```
tests/unit/domain/test_time_parser.py                  — 21 passed
tests/unit/use_cases/test_schedule_task_guardrail.py   —  6 passed
tests/unit/adapters/test_sqlite_scheduler_created_by.py —  6 passed
tests/unit/adapters/tools/test_scheduler_tool.py       — 42 passed
tests/unit/infrastructure/test_container_wire_scheduler.py — 6 passed

Exit code: 0
```

**Coverage**: Not measured — no `--cov` flag available in this run.

---

## Spec Compliance Matrix

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| REQ-ST-1 (registration) | SC-ST-10: wire idempotency | `test_container_wire_scheduler.py > test_wire_scheduler_idempotent` | ✅ COMPLIANT |
| REQ-ST-1 (name="scheduler") | — | `test_container_wire_scheduler.py > test_wire_scheduler_registers_tool_with_correct_config` | ✅ COMPLIANT |
| REQ-ST-1 (not registered before wire) | — | `test_container_wire_scheduler.py > test_wire_scheduler_noop_when_use_case_is_none` | ✅ COMPLIANT |
| REQ-ST-1 (no-op if disabled) | — | `test_container_wire_scheduler.py > test_wire_scheduler_noop_when_use_case_is_none` | ✅ COMPLIANT |
| REQ-ST-2 (create one_shot relative) | SC-ST-1 | `test_scheduler_tool.py > test_create_one_shot_relative_schedule` | ✅ COMPLIANT |
| REQ-ST-2 (create one_shot ISO) | SC-ST-2 | `test_scheduler_tool.py > test_create_one_shot_iso_schedule` | ✅ COMPLIANT |
| REQ-ST-2 (create recurring cron) | SC-ST-3 | `test_scheduler_tool.py > test_create_recurring_cron_schedule` | ✅ COMPLIANT |
| REQ-ST-2 (created_by injection) | SC-ST-1 | `test_scheduler_tool.py > test_create_created_by_always_from_agent_id_not_kwargs` | ✅ COMPLIANT |
| REQ-ST-2 (invalid trigger_type) | — | `test_scheduler_tool.py > test_create_invalid_trigger_type_is_error` | ✅ COMPLIANT |
| REQ-ST-2 (invalid trigger_payload) | — | `test_scheduler_tool.py > test_create_invalid_trigger_payload_is_error` | ✅ COMPLIANT |
| REQ-ST-3 (list all) | SC-ST-6 | `test_scheduler_tool.py > test_list_returns_correct_shape` | ✅ COMPLIANT |
| REQ-ST-3 (list empty = success) | SC-ST-6 | `test_scheduler_tool.py > test_list_empty_returns_zero_total` | ✅ COMPLIANT |
| REQ-ST-4 (get happy path) | — | `test_scheduler_tool.py > test_get_happy_path` | ✅ COMPLIANT |
| REQ-ST-4 (get not found) | — | `test_scheduler_tool.py > test_get_task_not_found` | ✅ COMPLIANT |
| REQ-ST-5 (update happy path) | SC-ST-7 | `test_scheduler_tool.py > test_update_happy_path` | ✅ COMPLIANT |
| REQ-ST-5 (update builtin protected) | — | `test_scheduler_tool.py > test_update_builtin_task_protected` | ✅ COMPLIANT |
| REQ-ST-5 (update relative schedule) | SC-ST-7 | `test_scheduler_tool.py > test_update_relative_schedule_parsed` | ✅ COMPLIANT |
| REQ-ST-5 (update no mutable fields) | — | `test_scheduler_tool.py > test_update_no_mutable_fields` | ✅ COMPLIANT |
| REQ-ST-6 (delete happy path) | — | `test_scheduler_tool.py > test_delete_happy_path` | ✅ COMPLIANT |
| REQ-ST-6 (delete builtin protected) | SC-ST-5 | `test_scheduler_tool.py > test_delete_builtin_task_protected` | ✅ COMPLIANT |
| REQ-ST-6 (delete not found) | — | `test_scheduler_tool.py > test_delete_task_not_found` | ✅ COMPLIANT |
| REQ-ST-7 (guardrail raises at 21) | SC-ST-4 | `test_schedule_task_guardrail.py > test_guardrail_raises_when_count_at_limit` | ✅ COMPLIANT |
| REQ-ST-7 (guardrail raises at 22) | SC-ST-4 | `test_schedule_task_guardrail.py > test_guardrail_raises_when_count_above_limit` | ✅ COMPLIANT |
| REQ-ST-7 (guardrail allows at 20) | SC-ST-4 | `test_schedule_task_guardrail.py > test_guardrail_allows_when_count_below_limit` | ✅ COMPLIANT |
| REQ-ST-7 (CLI tasks skip guardrail) | — | `test_schedule_task_guardrail.py > test_cli_task_skips_guardrail` | ✅ COMPLIANT |
| REQ-ST-7 (TooManyActiveTasksError → ToolResult) | SC-ST-4 | `test_scheduler_tool.py > test_create_too_many_active_tasks_error` | ✅ COMPLIANT |
| REQ-ST-8 (relative valid formats) | SC-ST-1, SC-ST-9-D | `test_time_parser.py > test_relative_offset_returns_utc_datetime[*]` | ✅ COMPLIANT |
| REQ-ST-8 (+0m reject) | SC-ST-9-A (resolved) | `test_time_parser.py > test_zero_duration_raises_value_error[+0m]` | ⚠️ PARTIAL (see WARNING W-1) |
| REQ-ST-8 (large offset +999d) | SC-ST-9-B | `test_time_parser.py > test_relative_offset_returns_utc_datetime[+999d]` | ✅ COMPLIANT |
| REQ-ST-8 (invalid format) | SC-ST-9-C | `test_time_parser.py > test_invalid_format_raises_value_error[*]` | ✅ COMPLIANT |
| REQ-ST-8 (cron pass-through for recurring) | SC-ST-3 | `test_scheduler_tool.py > test_create_recurring_cron_schedule` | ✅ COMPLIANT |
| REQ-ST-9 (wire_scheduler idempotency) | SC-ST-10 | `test_container_wire_scheduler.py > test_wire_scheduler_idempotent` | ✅ COMPLIANT |
| REQ-ST-9 (no-op if None) | SC-ST-10 | `test_container_wire_scheduler.py > test_wire_scheduler_noop_when_use_case_is_none` | ✅ COMPLIANT |
| REQ-ST-10 (unknown operation) | SC-ST-8 | `test_scheduler_tool.py > test_create_unknown_operation_is_error` | ✅ COMPLIANT |
| REQ-ST-10 (no exception escapes) | — | `test_scheduler_tool.py > test_create_unexpected_exception_returns_error` | ✅ COMPLIANT |
| REQ-ST-10 (domain errors → ToolResult) | SC-ST-5 | `test_scheduler_tool.py > test_delete_builtin_task_protected` | ✅ COMPLIANT |
| REQ-ST-11 (UserConfig.timezone) | — | structural only — config class + yaml example verified | ✅ COMPLIANT |
| REQ-ST-12 (created_by field) | SC-ST-1, SC-ST-6 | `test_sqlite_scheduler_created_by.py > test_ensure_schema_idempotent_adds_created_by` | ✅ COMPLIANT |
| REQ-ST-12 (migration idempotent) | — | `test_sqlite_scheduler_created_by.py > test_ensure_schema_idempotent_adds_created_by` | ✅ COMPLIANT |
| REQ-ST-12 (count isolation) | SC-ST-4, SC-ST-6 | `test_sqlite_scheduler_created_by.py > test_count_active_by_agent_counts_only_matching_agent` | ✅ COMPLIANT |

**Compliance summary**: 39/40 scenario/requirement checks compliant. 1 PARTIAL (W-1 below).

---

## Correctness (Static — Structural Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| REQ-ST-1: SchedulerTool at correct path, implements ITool | ✅ Implemented | `adapters/outbound/tools/scheduler_tool.py`, class `SchedulerTool(ITool)` |
| REQ-ST-1: name = "scheduler" | ✅ Implemented | Class attribute `name = "scheduler"` |
| REQ-ST-1: parameters_schema is valid JSON Schema | ✅ Implemented | Full schema with operation enum, all fields documented |
| REQ-ST-2: All required params validated for create | ✅ Implemented | name, task_kind, trigger_type, schedule, trigger_payload validated |
| REQ-ST-2: created_by never from LLM input | ✅ Implemented | `created_by=self._agent_id` in `_create`, LLM kwargs ignored |
| REQ-ST-3: List returns {"tasks":[...], "total":N} | ✅ Implemented | Matches design shape exactly |
| REQ-ST-3: List includes required fields | ✅ Implemented | id, name, task_kind, status, next_run_at, trigger_type, created_by |
| REQ-ST-4: Get returns full detail | ✅ Implemented | Includes trigger_payload, created_at, last_run |
| REQ-ST-5: Immutable fields silently dropped | ✅ Implemented | Only `_MUTABLE_FIELDS` processed; id, created_by, task_kind ignored |
| REQ-ST-6: Builtin guard (id < 100) in use case | ✅ Implemented | `ScheduleTaskUseCase.delete_task` and `update_task` check `task_id < 100` |
| REQ-ST-7: count_active_by_agent in ISchedulerRepository | ✅ Implemented | Added as last method in Protocol |
| REQ-ST-7: SQLite query excludes terminal statuses | ✅ Implemented | `NOT IN ('completed', 'failed', 'disabled')` |
| REQ-ST-8: Regex `^\+(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$` | ✅ Implemented | Exact regex in `time_parser.py` |
| REQ-ST-8: Bare `+` rejected with explicit guard | ✅ Implemented | All-None groups check before total_minutes computation |
| REQ-ST-9: _scheduler_wired flag | ✅ Implemented | `_scheduler_wired: bool = False` in `AgentContainer.__init__` |
| REQ-ST-9: AppContainer phase-3 loop | ✅ Implemented | After scheduler_service construction, iterates all agents |
| REQ-ST-10: Broad except in execute() | ✅ Implemented | Outer `except Exception as exc` in `execute()` |
| REQ-ST-10: No raise escapes execute() | ✅ Implemented | All paths return ToolResult |
| REQ-ST-11: UserConfig Pydantic model | ✅ Implemented | `UserConfig(timezone: str = "UTC")` in config.py |
| REQ-ST-11: GlobalConfig.user field | ✅ Implemented | `user: UserConfig = UserConfig()` |
| REQ-ST-11: global.example.yaml updated | ✅ Implemented | Full [user] section with commented examples including IANA timezone names |
| REQ-ST-11: user_timezone passed to wire_scheduler | ✅ Implemented | `user_timezone = global_config.user.timezone` in AppContainer |
| REQ-ST-12: created_by: str = "" on ScheduledTask | ✅ Implemented | Added after `status` field |
| REQ-ST-12: Migration in _ensure_schema_conn | ✅ Implemented | ALTER TABLE with try/except checking "duplicate column" |
| REQ-ST-12: created_by in all row mappings | ✅ Implemented | save_task insert (id==0), upsert, seed_builtin, _row_to_task all include created_by |

---

## Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| Single tool with operation enum (WebSearchTool pattern) | ✅ Yes | operation dispatches to `_create/_list/_get/_update/_delete` |
| parse_schedule in `core/domain/utils/time_parser.py` | ✅ Yes | Pure function, no external dependencies |
| Regex `^\+(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$` | ✅ Yes | Exact match |
| Recurring + relative guard at tool layer | ✅ Yes | Checked before parse_schedule call in `_create` |
| Guardrail in `ScheduleTaskUseCase.create_task` | ✅ Yes | Domain invariant in use case, CLI tasks (created_by="") skip |
| created_by default = "" | ✅ Yes | `ScheduledTask.created_by: str = ""` |
| UserConfig(timezone) on GlobalConfig | ✅ Yes | Placed before DelegationConfig, consistent ordering |
| wire_scheduler() mirrors wire_delegation() | ✅ Yes | Same pattern: lazy import, idempotency flag, per-agent try/except in AppContainer |
| trigger_payload update via get_task (design noted as unaddressed) | ✅ Yes | Implementation adds get_task call — correctly handles edge case |
| File Changes table — all 10 files created/modified | ✅ Yes | All 10 files verified in codebase |
| List response shape {"tasks":[...], "total":N} | ✅ Yes | Matches design contract exactly |
| consolidate_memory excluded from _ALLOWED_TRIGGER_TYPES | ✅ Yes | System-only trigger type excluded |
| TaskKind mapping: domain "oneshot"/"recurrent" ↔ LLM "one_shot"/"recurring" | ✅ Yes | `_TASK_KIND_TO_LLM` / `_LLM_TO_TASK_KIND` dicts, round-trip tested |

---

## Issues Found

### CRITICAL
None.

---

### WARNING

**W-1 — REQ-ST-8 AC3 vs Resolved Questions contradiction**

`spec.md` REQ-ST-8 AC3 states:
> "`+0m` is a valid input (interpreted as 'now' — resolves to `datetime.utcnow()`). This is allowed and passes through to the use case."

However, the "Resolved Questions" section at the bottom of `spec.md` states:
> "**`+0m` semantics**: RESOLVED — Tool rejects `+0m` and any relative offset resolving to zero duration. Input validation in the tool layer."

The design (`design.md`) and implementation both reject `+0m` with `ValueError("must have a positive duration")`. The tests in `test_time_parser.py` confirm rejection of `+0m`, `+0d`, `+0h`, etc.

The Resolved Questions section takes precedence over the draft acceptance criteria (this is standard SDD priority), and the behavior is internally consistent across design, implementation, and tests. However, the spec body (REQ-ST-8 AC3) was never updated to reflect the resolution — it still says `+0m` is allowed.

**Action**: Update REQ-ST-8 AC3 in `spec.md` to align with the Resolved Questions decision before archiving. Not a code defect — the implementation is correct.

---

**W-2 — Two new mypy errors in scheduler_tool.py (false positives)**

Lines 262 and 447 in `scheduler_tool.py`:
```python
trigger_payload_obj = payload_model_cls.model_validate(trigger_payload_raw)
```
mypy reports `"ModelMetaclass" has no attribute "model_validate"` because the `_TRIGGER_PAYLOAD_MODELS` dict type is inferred as `dict[str, type[ChannelSendPayload] | type[AgentSendPayload] | type[ShellExecPayload]]` which mypy resolves to `ModelMetaclass`. The code is functionally correct — `model_validate` exists on all Pydantic model classes and all tests pass.

**Fix** (optional): add a `cast()` or type annotation to help mypy:
```python
_TRIGGER_PAYLOAD_MODELS: dict[str, type[BaseModel]] = { ... }
```
Not a blocker.

---

**W-3 — REQ-ST-11 AC6: user_timezone NOT included in tool description**

REQ-ST-11 AC6 states:
> "The tool description MAY reference `user_timezone` as static text (e.g. 'User's local timezone: America/Argentina/Buenos_Aires') — this is set once at tool construction, NOT regenerated per LLM call."

The `SchedulerTool.description` is a class-level string constant and does NOT include the user_timezone value. The constructor accepts `user_timezone` and stores it (`self._user_timezone`) but the description text is not dynamically set at construction time.

This is a MAY requirement, so it is not a spec violation. However the stored `_user_timezone` is only used internally by `parse_schedule` and is not surfaced to the LLM in the description.

**Action**: Consider setting `self.description` in `__init__` to include the timezone string. Low priority.

---

### SUGGESTION

**S-1 — SC-ST-9 Case A lacks a dedicated test for the "rejected" behavior path**

SC-ST-9 Case A in the spec describes the *original* behavior (allowed), but the code rejects `+0m`. The test `test_zero_duration_raises_value_error[+0m]` in `test_time_parser.py` covers the rejection, but there is no test in `test_scheduler_tool.py` that explicitly tests the full `create` operation with `+0m` returning `ToolResult(success=False)`. The coverage is adequate through the time_parser tests, but an end-to-end tool-layer test would complete the scenario.

**S-2 — SC-ST-10 not covered for the "AppContainer phase-3 loop" path**

All wire_scheduler tests use `AgentContainer` directly (`__new__` pattern). There is no integration test that exercises the AppContainer phase-3 wiring loop end-to-end. The AppContainer test suite (if any) may not cover this. This is a suggestion, not a blocker — the unit tests for `wire_scheduler` directly are thorough.

**S-3 — REQ-ST-4 has no scenario in the traceability matrix**

The spec's traceability matrix notes "REQ-ST-4 → — (covered by SC-ST-5 not-found path implicitly)". The `get` happy path (SC equivalent) has no dedicated scenario. A formal SC-ST for `get` happy path (success=True, full serialization) would complete traceability.

---

## Verdict

**APPROVED WITH WARNINGS**

All 14 tasks implemented, all 81 tests pass (exit code 0). All 12 requirements have structural evidence of correct implementation. No critical spec violations. Three warnings: one spec body inconsistency (W-1, non-code), two minor type/description issues (W-2, W-3). Three non-blocking suggestions.

The implementation is ready for archive after addressing W-1 (spec body update only — no code changes required).
