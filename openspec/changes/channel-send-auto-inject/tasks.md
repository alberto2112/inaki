# Tasks: channel-send-auto-inject

## Dependency Order

```
T1 (ChannelContext VO)
  └── T2 (ChannelSendPayload refactor)
        └── T4 (SchedulerTool injection)
              └── T6 (wire_scheduler + AgentContainer context holder)
                    ├── T7 (Telegram inbound adapter)
                    ├── T8 (CLI inbound adapter)
                    └── T9 (Integration test: inbound → tool → stored target)

T3 (ChannelSenderAdapter fix)   ← puede ir en paralelo con T1-T2
  └── T5 (AppContainer telegram_gateway)
        └── T6 (wire_scheduler + AgentContainer context holder)
```

Tareas paralelizables:
- **T1** y **T3** pueden arrancar simultáneamente (no dependen entre sí)
- **T7** y **T8** pueden ejecutarse en paralelo entre sí (ambas dependen solo de T6)

---

## Tasks

### T1: ChannelContext value object

- **Files**:
  - `core/domain/value_objects/channel_context.py` (nuevo)
- **Test files**:
  - `tests/unit/domain/test_channel_context.py` (nuevo)
- **What**: Crear `ChannelContext` como Pydantic v2 BaseModel frozen=True con campos
  `channel_type: str`, `user_id: str`, y propiedad `routing_key` que retorna
  `f"{channel_type}:{user_id}"`. Validación: ambos campos no pueden ser cadenas vacías.
- **Accept**:
  - CS-1-A: `ChannelContext("telegram","123456").routing_key == "telegram:123456"`
  - CS-1-B: `ChannelContext("cli","local").routing_key == "cli:local"`
  - CS-1-C: `channel_type=""` → `ValidationError`
  - CS-1-D: `user_id=""` → `ValidationError`
  - `frozen=True`: cualquier intento de mutación lanza `ValidationError`
  - `__init__.py` de `core/domain/value_objects/` exporta `ChannelContext`

---

### T2: ChannelSendPayload — renombre channel_id → target y campo user_id opcional

- **Depends on**: T1
- **Files**:
  - `core/domain/entities/task.py` (modificar `ChannelSendPayload`)
- **Test files**:
  - `tests/unit/domain/test_webhook_payload.py` → agregar casos `ChannelSendPayload`
    (o crear `tests/unit/domain/test_channel_send_payload.py` si se prefiere)
- **What**: Renombrar `channel_id: str` → `target: str` en `ChannelSendPayload`.
  Agregar `user_id: str | None = None` como campo opcional para override del LLM.
  No agregar `model_config` alias — el campo `channel_id` simplemente desaparece.
- **Accept**:
  - CS-2-A: `model_dump()` contiene `target`, no contiene `channel_id`
  - CS-2-B: `user_id` presente en serialización cuando tiene valor
  - CS-2-C: `model_validate({"type": "channel_send", "channel_id": "x", "text": "y"})` → `ValidationError`
  - CS-2-D: `model_validate({"type": "channel_send", "target": "telegram:42", "text": "hola"})` → OK

---

### T3: ChannelSenderAdapter — fix bug telegram_gateway + renombre target + canales no soportados

- **Depends on**: ninguna (paralelo a T1)
- **Files**:
  - `adapters/outbound/scheduler/dispatch_adapters.py` (modificar `ChannelSenderAdapter`)
- **Test files**:
  - `tests/unit/adapters/scheduler/test_dispatch_adapters.py` (ampliar tests existentes)
- **What**: Renombrar parámetro `channel_id` → `target` en `send_message()`.
  Reemplazar `self._container.telegram_gateway` por un callable inyectado
  `get_telegram_gateway: Callable[[], TelegramGateway | None]` pasado al constructor.
  Agregar casos: "cli:..." y "daemon:..." → `ValueError` descriptivo.
  Telegram no configurado (gateway es None) → `ValueError` descriptivo en lugar de `AttributeError`.
- **Accept**:
  - CS-7-A: gateway disponible → `gateway.send_message(int(user_id), text)` llamado
  - CS-7-B: gateway es None → `ValueError("Telegram gateway no configurado...")`
  - CS-7-C: `target="cli:local"` → `ValueError("Canal cli: no soporta despacho programado")`
  - CS-7-D: `target="daemon:system"` → mismo `ValueError`
  - CS-7-E: prefijo desconocido → `ValueError("Unknown channel prefix: ...")`

---

### T4: SchedulerTool — inyección de ChannelContext en _create() y _update()

- **Depends on**: T1, T2
- **Files**:
  - `adapters/outbound/tools/scheduler_tool.py`
- **Test files**:
  - `tests/unit/adapters/tools/test_scheduler_tool.py` (ampliar tests existentes)
- **What**: Agregar parámetro `get_channel_context: Callable[[], ChannelContext | None]`
  al constructor de `SchedulerTool`. En `_create()`, para `trigger_type == "channel_send"`:
  (1) leer `get_channel_context()`, si es None → retornar `ToolResult(success=False)` con mensaje;
  (2) determinar `effective_user_id`: LLM override (`trigger_payload_raw.get("user_id")`) > `ctx.user_id`;
  (3) construir `target = f"{ctx.channel_type}:{effective_user_id}"` e inyectarlo en
  `trigger_payload_raw` antes de `model_validate`; (4) eliminar "channel_id" si el LLM lo proveyó.
  Aplicar la misma lógica en `_update()` cuando el trigger type de la tarea existente es `channel_send`.
  Actualizar `parameters_schema`: eliminar "channel_id" de la descripción de `channel_send`,
  documentar `user_id` como campo opcional.
- **Accept**:
  - CS-4-A: sin override → `target == ctx.routing_key`
  - CS-4-B: LLM provee `user_id="999"` → `target == f"{ctx.channel_type}:999"`
  - CS-4-C: `get_channel_context()` retorna None → `ToolResult(success=False)` con mensaje claro
  - CS-4-D: `trigger_type != "channel_send"` → `get_channel_context()` no consultado
  - CS-4-E: `_update()` con tarea `channel_send` → misma inyección
  - CS-5-A: `parameters_schema` no contiene la cadena "channel_id"
  - CS-5-B: `parameters_schema` documenta `channel_send` con `text` y `user_id` (opcional)

---

### T5: AppContainer — exponer telegram_gateway

- **Depends on**: T3
- **Files**:
  - `infrastructure/container.py` (modificar `AppContainer.__init__`)
- **Test files**:
  - `tests/unit/infrastructure/test_container.py` (ampliar) o
    `tests/unit/infrastructure/test_container_wire_scheduler.py` (ampliar)
- **What**: Agregar `self.telegram_gateway: TelegramGateway | None = None` a `AppContainer`.
  Inicializarlo durante la construcción de agentes: cuando un agente tiene
  `channels.telegram.token` configurado, construir (o reusar) la instancia de
  `TelegramGateway` y asignarla a `self.telegram_gateway`.
  Actualizar `ChannelSenderAdapter` para recibir el callable
  `get_telegram_gateway: Callable[[], TelegramGateway | None]` y pasarle
  `lambda: self.telegram_gateway` desde `AppContainer.__init__`.
- **Accept**:
  - CS-8-D: `AppContainer` con agente Telegram configurado → `telegram_gateway` no es None
  - Sin agente Telegram → `telegram_gateway is None`
  - `ChannelSenderAdapter` ya no accede a `self._container` directamente (no más `AttributeError`)

---

### T6: AgentContainer — ChannelContextHolder + set_channel_context + wire_scheduler actualizado

- **Depends on**: T1, T4, T5
- **Files**:
  - `infrastructure/container.py` (modificar `AgentContainer`)
- **Test files**:
  - `tests/unit/infrastructure/test_container_wire_scheduler.py` (ampliar)
- **What**: En `AgentContainer.__init__` crear `self._channel_context: ChannelContext | None = None`.
  Agregar método `set_channel_context(ctx: ChannelContext) -> None` que asigna el atributo.
  Agregar método `get_channel_context() -> ChannelContext | None` que lo retorna.
  En `wire_scheduler()` pasar `get_channel_context=self.get_channel_context` al constructor de
  `SchedulerTool`.
- **Accept**:
  - CS-8-A: `AgentContainer` nuevo → `get_channel_context()` retorna None
  - CS-8-B: `set_channel_context(ChannelContext("telegram","5"))` → `get_channel_context().routing_key == "telegram:5"`
  - CS-8-C: `wire_scheduler()` construye `SchedulerTool` con `get_channel_context` pasado correctamente
  - CS-3-C: dos llamadas sucesivas a `set_channel_context` → la segunda sobreescribe a la primera

---

### T7: Telegram inbound adapter — set_channel_context antes de execute()

- **Depends on**: T6
- **Files**:
  - `adapters/inbound/telegram/bot.py`
- **Test files**:
  - `tests/unit/adapters/test_telegram_message_mapper.py` (si aplica) o
    nuevo `tests/unit/adapters/test_telegram_bot_channel_context.py`
- **What**: En `TelegramBot._handle_message()`, antes de llamar
  `self._container.run_agent.execute(user_input)`, construir
  `ChannelContext("telegram", str(update.effective_user.id))` y llamar
  `self._container.set_channel_context(ctx)`. Limpiar con `set_channel_context(None)` en el
  bloque `finally` (opcional pero recomendado para evitar fugas entre handlers).
  Aplicar la misma lógica en `_cmd_consolidate` si aplica (no es necesario para `channel_send`,
  pero es consistente).
- **Accept**:
  - CS-6-A: `update.effective_user.id == 42` → `set_channel_context` llamado con
    `ChannelContext("telegram", "42")` antes de `run_agent.execute()`
  - Si `execute()` lanza excepción → context no queda "sucio" para el próximo handler
    (gracias al finally)

---

### T8: CLI inbound adapter — set_channel_context al inicio de sesión

- **Depends on**: T6
- **Files**:
  - `adapters/inbound/cli/cli_runner.py`
- **Test files**:
  - No hay test unitario del CLI runner actualmente; agregar
    `tests/unit/adapters/test_cli_runner_channel_context.py` con mock mínimo
- **What**: En `run_cli()`, después de obtener `container = app.get_agent(agent_id)`,
  llamar `container.set_channel_context(ChannelContext("cli", "local"))` una sola vez
  (no por mensaje — el contexto CLI es estático para toda la sesión).
- **Accept**:
  - CS-6-B: `container.set_channel_context` llamado con `ChannelContext("cli", "local")`
    exactamente una vez al inicio, antes del loop de mensajes
  - El contexto persiste para todos los mensajes de la sesión sin re-asignación

---

### T9: Test de integración — flujo completo Telegram → context → stored target

- **Depends on**: T7 (o T6 como mínimo)
- **Files**:
  - `tests/integration/scheduler/test_channel_send_auto_inject.py` (nuevo)
- **Test files**: el archivo es el test
- **What**: Test de integración que verifica el flujo completo:
  (1) crear `AgentContainer` con `wire_scheduler` mockeado;
  (2) `set_channel_context(ChannelContext("telegram", "99999"))`;
  (3) llamar `SchedulerTool._create()` con `trigger_type="channel_send"`,
      `trigger_payload={"text": "recordatorio"}` (sin `target` ni `channel_id`);
  (4) verificar que la tarea almacenada tiene `trigger_payload.target == "telegram:99999"`.
- **Accept**:
  - La tarea se crea exitosamente sin que el LLM provea `target`
  - `stored_task.trigger_payload.target == "telegram:99999"`
  - Sin `set_channel_context` previo → `ToolResult(success=False)` con mensaje descriptivo

---

## Resumen de archivos afectados

| Archivo | Acción | Tarea |
|---------|--------|-------|
| `core/domain/value_objects/channel_context.py` | crear | T1 |
| `core/domain/entities/task.py` | modificar `ChannelSendPayload` | T2 |
| `adapters/outbound/scheduler/dispatch_adapters.py` | modificar `ChannelSenderAdapter` | T3 |
| `adapters/outbound/tools/scheduler_tool.py` | agregar inyección + schema | T4 |
| `infrastructure/container.py` | `AppContainer.telegram_gateway` | T5 |
| `infrastructure/container.py` | `AgentContainer` context holder + `wire_scheduler` | T6 |
| `adapters/inbound/telegram/bot.py` | `set_channel_context` en handler | T7 |
| `adapters/inbound/cli/cli_runner.py` | `set_channel_context` al inicio | T8 |
| `tests/unit/domain/test_channel_context.py` | crear | T1 |
| `tests/unit/domain/test_channel_send_payload.py` | crear | T2 |
| `tests/unit/adapters/scheduler/test_dispatch_adapters.py` | ampliar | T3 |
| `tests/unit/adapters/tools/test_scheduler_tool.py` | ampliar | T4 |
| `tests/unit/infrastructure/test_container_wire_scheduler.py` | ampliar | T5+T6 |
| `tests/unit/adapters/test_cli_runner_channel_context.py` | crear | T8 |
| `tests/integration/scheduler/test_channel_send_auto_inject.py` | crear | T9 |
