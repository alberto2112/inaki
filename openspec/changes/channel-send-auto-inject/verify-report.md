# Verify Report: channel-send-auto-inject

**Date**: 2026-04-13  
**Verifier**: sdd-verify sub-agent  
**Test run**: `pytest tests/unit/ tests/integration/scheduler/test_channel_send_auto_inject.py`  
**Result**: 523 passed, 11 failed (pre-existing failures unrelated to this change), 1 warning

---

## Summary

The implementation is **substantially complete** and functionally correct for the happy paths. Two issues found:

- **CRITICAL**: REQ-CS-4-E — `_update()` does NOT inject channel context for `channel_send` payloads. The spec required the same injection logic as `_create()`. No test covers this case.
- **WARNING**: REQ-CS-7-B — `ChannelSenderAdapter` does not guard against `bot is None`. If telegram is not configured, `await bot.send_message(...)` raises `AttributeError`, not the required descriptive `ValueError`.

---

## Requirements Verification

### REQ-CS-1: ChannelContext VO — PASS

**Evidence**:
- File: `core/domain/value_objects/channel_context.py`
- Pydantic v2 `BaseModel(frozen=True)`, fields `channel_type: str`, `user_id: str`
- `routing_key` as `@computed_field @property` → `f"{channel_type}:{user_id}"`
- `@field_validator` rejects empty or whitespace-only strings on both fields

**Scenarios covered**:
- CS-1-A: `test_construccion_valida_produce_routing_key_correcto` ✅
- CS-1-B: `test_channel_type_cli_routing_key` ✅
- CS-1-C: `test_channel_type_vacio_lanza_error` ✅
- CS-1-D: `test_user_id_vacio_lanza_error` ✅
- Frozen: `test_no_se_puede_mutar_channel_type`, `test_no_se_puede_mutar_user_id` ✅

**Note**: Spec said `core/domain/entities/channel_context.py`; implementation uses `core/domain/value_objects/channel_context.py`. Design document is the correct reference — value_objects is the right location.

---

### REQ-CS-2: ChannelSendPayload refactor — PASS

**Evidence**:
- File: `core/domain/entities/task.py:36-40`
- `target: str`, `text: str`, `user_id: str | None = None` — no `channel_id` field

**Scenarios covered**:
- CS-2-A: `test_construccion_con_target_y_text` — `model_dump()` has `target` ✅
- CS-2-B: `test_construccion_con_target_text_y_user_id` ✅
- CS-2-C: `test_channel_id_no_existe` — `hasattr(payload, "channel_id")` → False ✅

**Note**: Spec scenario CS-2-C asked for `model_validate({"channel_id": ...}) → ValidationError`. The test checks `hasattr` instead. This is functionally equivalent (field doesn't exist) but the scenario description is not precisely covered. Minor gap.

---

### REQ-CS-3: ChannelContext lifecycle / AgentContainer context holder — PASS

**Evidence**:
- File: `infrastructure/container.py:65-155`
- `self._channel_context: ChannelContext | None = None` initialized in `__init__`
- `set_channel_context(ctx)` and `get_channel_context()` methods present
- `wire_scheduler` passes `self.get_channel_context` (not a lambda) to `SchedulerTool`

**Scenarios covered**:
- CS-3-A: `test_wire_scheduler_pasa_get_channel_context` — sets context, tool reads it ✅
- CS-3-B: `test_get_channel_context_devuelve_none_inicialmente` ✅
- CS-3-C: `test_set_channel_context_none_limpia_contexto` — two sequential sets ✅

---

### REQ-CS-4: SchedulerTool injection — PARTIAL

**Evidence — PASS for _create()**:
- File: `adapters/outbound/tools/scheduler_tool.py:267-282`
- `_create()`: reads context, returns error if None, discards LLM `target`, handles `user_id` override
- Tests: `test_create_channel_send_target_auto_inyectado_desde_contexto` (CS-4-A) ✅
- Tests: `test_create_channel_send_user_id_override_reconstruye_target` (CS-4-B) ✅
- Tests: `test_create_channel_send_sin_contexto_retorna_error` (CS-4-C) ✅
- Tests: `test_create_no_channel_send_sin_inyeccion` (CS-4-D) ✅
- Tests: `test_create_channel_send_target_en_payload_descartado_silenciosamente` ✅

**CRITICAL — FAIL for _update() (CS-4-E)**:
- File: `adapters/outbound/tools/scheduler_tool.py:454-477`
- `_update()` handles `trigger_payload` updates by calling `payload_model_cls.model_validate(payload_raw)` directly — NO channel context injection for `channel_send` payloads.
- If the LLM calls `update` to change the `text` of a `channel_send` task, it must supply `target` in the payload or validation fails (since `target` is a required field on `ChannelSendPayload`).
- No test covers this scenario.
- **Issue**: CRITICAL — spec CS-4-E is not implemented. A LLM `update` on a `channel_send` task payload will fail unless the LLM provides `target`, which it is not supposed to do.

---

### REQ-CS-5: LLM schema — PASS

**Evidence**:
- File: `adapters/outbound/tools/scheduler_tool.py:128-130`
- Schema description: `"For 'channel_send': {\"text\": \"...\", \"user_id\": \"...(opcional)\"}. El canal de destino se inyecta automáticamente del contexto de conversación — NO incluir 'channel_id' ni 'target'."`

**Scenarios covered**:
- CS-5-A: No `"channel_id"` in `parameters_schema` ✅ (grep confirms only appears in the warning string)
- CS-5-B: Documents `channel_send` with `text` + optional `user_id` ✅

---

### REQ-CS-6: Inbound adapters — PASS (Telegram + CLI)

**Telegram** (`adapters/inbound/telegram/bot.py:97-124`):
- `set_channel_context(ChannelContext(channel_type="telegram", user_id=str(update.effective_user.id)))` called before `execute()`
- `set_channel_context(None)` in `finally` block (always clears, even on exception)
- CS-6-A: Covered by 5 tests in `test_telegram_bot_channel_context.py` ✅

**CLI** (`adapters/inbound/cli/cli_runner.py:32-106`):
- `container.set_channel_context(ChannelContext(channel_type="cli", user_id="local"))` once before loop
- `set_channel_context(None)` in `finally` block
- CS-6-B: Covered by 4 tests in `test_cli_runner_channel_context.py` ✅

**REST / Daemon**: Not verified — scope was not included in tasks (T7 only covers Telegram, T8 covers CLI). Daemon runner was updated to register bots but does not set channel context itself (daemon doesn't initiate conversations — by design).

---

### REQ-CS-7: ChannelSenderAdapter — PARTIAL

**Evidence**:
- File: `adapters/outbound/scheduler/dispatch_adapters.py:19-49`
- Constructor accepts `get_telegram_bot: Callable` (not `app_container`)
- `_CANALES_INBOUND = {"cli", "rest", "daemon"}` → `ValueError` for these
- Unknown prefix → `ValueError("Prefijo de canal desconocido: ...")`
- Covered by tests in `tests/unit/adapters/scheduler/test_dispatch_adapters.py`

**Scenarios covered**:
- CS-7-A: `test_telegram_prefix_llama_send_message_con_user_id_entero` ✅
- CS-7-C: `test_cli_prefix_lanza_value_error_descriptivo` ✅
- CS-7-D: `test_prefijo_desconocido_lanza_value_error` ✅

**WARNING — CS-7-B**:
- Spec: "telegram no configurado → ValueError descriptivo (no AttributeError)"
- Implementation: `bot = self._get_telegram_bot(); await bot.send_message(...)` — if `bot` is `None`, this raises `AttributeError: 'NoneType' object has no attribute 'send_message'`
- No guard against `bot is None` before calling `send_message`
- No test for this scenario
- **Issue**: WARNING — not a crash in production if telegram is configured, but the spec guarantees a descriptive `ValueError` which is not delivered.

---

### REQ-CS-8: Wiring in container.py — PASS

**Evidence**:
- File: `infrastructure/container.py`
- `AgentContainer.__init__`: `self._channel_context: ChannelContext | None = None` (line 70)
- `AgentContainer.set_channel_context()` (line 149), `get_channel_context()` (line 153)
- `AgentContainer.wire_scheduler()`: passes `get_channel_context=self.get_channel_context` (line 246)
- `AppContainer.__init__`: `self._telegram_bots: dict[str, object] = {}` (line 397)
- `AppContainer.register_telegram_bot()` (line 462), `_get_telegram_bot()` (line 471)
- `ChannelSenderAdapter(get_telegram_bot=self._get_telegram_bot)` (line 441)

**Scenarios covered**:
- CS-8-A: `test_channel_context_inicializa_en_none` ✅
- CS-8-B: `test_get_channel_context_devuelve_contexto_seteado` ✅
- CS-8-C: `test_wire_scheduler_pasa_get_channel_context` ✅
- CS-8-D: `tests/unit/infrastructure/test_container_telegram_gateway.py` (6 tests) ✅

---

## Stale Reference Check

**`channel_id` in production code**: None found.

Only references:
- `tests/unit/domain/test_channel_send_payload.py` — tests that `channel_id` does NOT exist (correct)
- `adapters/outbound/tools/scheduler_tool.py:130` — schema description warning LLM not to use it (correct)

---

## Architectural Compliance

- `core/` imports: No imports from `adapters/` or `infrastructure/` in any modified core file ✅
- `ChannelContext` lives in `core/domain/value_objects/` (correct layer) ✅
- `container.py` is the single wiring point for all new dependencies ✅
- `ChannelContext` uses `TYPE_CHECKING` guard in `container.py` to avoid circular imports ✅
- All production variables, docstrings, and comments in Spanish ✅

---

## Test Coverage Summary

| File | Tests | Status |
|------|-------|--------|
| `tests/unit/domain/test_channel_context.py` | 11 | ✅ all pass |
| `tests/unit/domain/test_channel_send_payload.py` | 5 | ✅ all pass |
| `tests/unit/infrastructure/test_container_channel_context.py` | 7 | ✅ all pass |
| `tests/unit/adapters/test_telegram_bot_channel_context.py` | 5 | ✅ all pass |
| `tests/unit/adapters/test_cli_runner_channel_context.py` | 4 | ✅ all pass |
| `tests/unit/adapters/scheduler/test_dispatch_adapters.py` | 9 (CSA) | ✅ all pass |
| `tests/unit/adapters/tools/test_scheduler_tool.py` | T4 scenarios | ✅ all pass |
| `tests/integration/scheduler/test_channel_send_auto_inject.py` | 4 | ✅ all pass |
| **Total change-related** | **57** | **✅ all pass** |

---

## Issues

### CRITICAL

**CS-4-E: `_update()` does not inject channel context for `channel_send`**
- File: `adapters/outbound/tools/scheduler_tool.py:454-477`
- The `_update()` method resolves the existing task's `trigger_type`, then calls `model_validate(payload_raw)` directly without injecting `target` from context.
- A LLM update call like `{"operation": "update", "task_id": 1, "trigger_payload": {"text": "nuevo texto"}}` on a `channel_send` task will fail with a validation error (missing required `target`).
- **Fix required**: In `_update()`, after resolving `trigger_type_str == "channel_send"`, apply the same context injection logic as `_create()` (read context, pop `target`, handle `user_id` override, inject `target`). Also add a test for this scenario.

### WARNING

**CS-7-B: `ChannelSenderAdapter` does not handle `bot is None`**
- File: `adapters/outbound/scheduler/dispatch_adapters.py:40-42`
- When `self._get_telegram_bot()` returns `None`, `await bot.send_message(...)` raises `AttributeError`, not `ValueError`.
- **Fix**: Add guard: `if bot is None: raise ValueError("Telegram no está configurado. El bot no fue registrado.")`
- **Severity**: Medium — only triggered when telegram is not configured AND a `channel_send` task fires, which is an invalid configuration state. Not a regression from the pre-change code.

### SUGGESTION

**CS-2-C test is weaker than spec scenario**
- Test: `assert not hasattr(payload, "channel_id")` 
- Spec: `model_validate({"channel_id": ...}) → ValidationError`
- Both pass but the spec scenario is more precise. Adding a strict validation test would be cleaner.

---

## Verdict

**PARTIAL PASS** — The implementation is functionally correct for all primary use cases (scheduling new `channel_send` tasks from conversations). The CRITICAL gap (CS-4-E) affects `_update()` of `channel_send` payloads, which is a secondary workflow but was explicitly required by the spec. The WARNING (`bot is None`) is a safety gap that won't manifest in correctly configured deployments.

**Recommended action**: Fix the two issues before archiving.
