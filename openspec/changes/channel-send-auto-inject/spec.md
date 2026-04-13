# Specs: channel-send-auto-inject

## Contexto

El trigger `channel_send` requiere una clave de routing (`target`) que el LLM no puede conocer
— es un detalle de infraestructura (e.g. `"telegram:123456"`). Este cambio elimina ese campo del
esquema LLM, lo inyecta automáticamente desde el contexto del canal activo, y repara el
`ChannelSenderAdapter` que referencia un atributo inexistente en `AppContainer`.

---

## Requirements

### REQ-CS-1: Value object ChannelContext

Se introduce `ChannelContext` como value object inmutable en `core/domain/entities/channel_context.py`.
Encapsula el tipo de canal y el identificador del usuario receptor, y expone una propiedad `routing_key`
que produce la cadena `"{channel_type}:{user_id}"` usada en `ChannelSendPayload.target`.

**Definición esperada:**
```python
class ChannelContext(BaseModel):
    channel_type: str   # e.g. "telegram", "cli", "rest", "daemon"
    user_id: str        # e.g. "123456", "local", "system"

    @property
    def routing_key(self) -> str:
        return f"{self.channel_type}:{self.user_id}"
```

Restricciones:
- Pydantic v2 BaseModel (inmutable — `model_config = ConfigDict(frozen=True)`)
- Vive en `core/` — no importa nada de `adapters/` ni `infrastructure/`
- `channel_type` y `user_id` no pueden ser cadenas vacías (validación con `@field_validator`)

**Scenarios:**

- **CS-1-A** — Dado `ChannelContext(channel_type="telegram", user_id="123456")`,
  cuando se accede a `.routing_key`, entonces retorna `"telegram:123456"`.

- **CS-1-B** — Dado `ChannelContext(channel_type="cli", user_id="local")`,
  cuando se accede a `.routing_key`, entonces retorna `"cli:local"`.

- **CS-1-C** — Dado un intento de construir `ChannelContext(channel_type="", user_id="x")`,
  cuando se valida el modelo, entonces Pydantic lanza `ValidationError`.

- **CS-1-D** — Dado un intento de construir `ChannelContext(channel_type="telegram", user_id="")`,
  cuando se valida el modelo, entonces Pydantic lanza `ValidationError`.

---

### REQ-CS-2: Renombre de campo en ChannelSendPayload

`ChannelSendPayload.channel_id: str` se renombra a `target: str` en
`core/domain/entities/task.py`. El campo sigue siendo `str` internamente (routing key completo).
Se agrega `user_id: str | None = None` como campo opcional para que `SchedulerTool` lo use
como override antes de construir el `target`.

**Definición esperada:**
```python
class ChannelSendPayload(BaseModel):
    type: Literal["channel_send"] = "channel_send"
    target: str          # routing key completo: "{channel_type}:{user_id}" — nunca viene del LLM
    text: str
    user_id: str | None = None  # override del usuario destino — puede venir del LLM
```

Restricciones:
- `target` es **siempre** inyectado por `SchedulerTool`, nunca por el LLM
- `user_id` puede ser provisto por el LLM para sobrescribir el usuario destino
- El campo `user_id` del payload es auxiliar para construcción — `target` es la fuente de verdad para despacho
- No hay migración de tareas existentes en SQLite (usuario elimina DB vieja)

**Scenarios:**

- **CS-2-A** — Dado `ChannelSendPayload(target="telegram:123456", text="Hola")`,
  cuando se serializa con `.model_dump()`, entonces el dict contiene `{"type": "channel_send", "target": "telegram:123456", "text": "Hola", "user_id": null}`.

- **CS-2-B** — Dado `ChannelSendPayload(target="telegram:123456", text="Hola", user_id="999")`,
  cuando se serializa, entonces `user_id` es `"999"` (override presente).

- **CS-2-C** — Dado `ChannelSendPayload.model_validate({"type": "channel_send", "channel_id": "x", "text": "y"})`,
  cuando se valida, entonces Pydantic lanza `ValidationError` (campo obsoleto `channel_id` no existe).

---

### REQ-CS-3: Ciclo de vida de ChannelContext

`ChannelContext` se crea en el adaptador inbound al inicio de cada conversación/request y se
propaga hasta `SchedulerTool` a través de un **context holder mutable compartido** inyectado en
construcción. El holder es un objeto con un único atributo `channel_context: ChannelContext | None`
que se setea por cada ciclo de ejecución.

**Componentes:**
- `ChannelContextHolder` — contenedor mutable simple en `core/domain/entities/channel_context.py`
- Instanciado una vez por `AgentContainer` en `__init__`
- Pasado a `SchedulerTool` en `wire_scheduler`
- Pasado a `RunAgentUseCase` (o leído indirectamente) para que el inbound adapter pueda setearlo antes de `execute()`

**Ciclo por request:**
1. Inbound adapter recibe request → construye `ChannelContext`
2. Inbound adapter llama `container.set_channel_context(ctx)` (método en `AgentContainer`)
3. `RunAgentUseCase.execute(user_input)` se invoca → dentro del tool loop, si el LLM llama `scheduler`, `SchedulerTool._create` lee el holder
4. Al finalizar el request, el holder queda con el último contexto (no se limpia — se sobreescribe en el siguiente request)

**Scenarios:**

- **CS-3-A** — Dado que el adaptador Telegram llama `container.set_channel_context(ChannelContext("telegram", "123"))`,
  cuando `SchedulerTool._create` lee el holder, entonces `holder.channel_context.routing_key == "telegram:123"`.

- **CS-3-B** — Dado que `set_channel_context` nunca fue llamado (holder en `None`),
  cuando `SchedulerTool._create` intenta crear una tarea `channel_send`,
  entonces retorna `ToolResult(success=False)` con mensaje de error indicando que no hay contexto de canal activo.

- **CS-3-C** — Dado que dos requests sucesivos llegan (primero Telegram user=1, luego Telegram user=2),
  cuando el segundo request ejecuta `SchedulerTool._create`,
  entonces el `target` inyectado corresponde al segundo contexto (`"telegram:2"`).

---

### REQ-CS-4: Inyección de target en SchedulerTool

`SchedulerTool` acepta un `ChannelContextHolder` en su constructor. En `_create()`, cuando
`trigger_type == "channel_send"`:

1. Lee `holder.channel_context` — si es `None`, retorna error
2. Determina el `user_id` efectivo: si el LLM proveyó `trigger_payload.user_id`, lo usa; si no, usa `holder.channel_context.user_id`
3. Construye `target = f"{holder.channel_context.channel_type}:{effective_user_id}"`
4. Inyecta `target` en `trigger_payload_raw` antes de la validación con Pydantic
5. Elimina `user_id` de `trigger_payload_raw` antes de pasar a `ChannelSendPayload.model_validate` (o lo preserva como campo auxiliar si el modelo lo acepta)

El mismo flujo aplica en `_update()` cuando `trigger_payload` es actualizado en una tarea `channel_send`.

**Scenarios:**

- **CS-4-A** — Dado `holder.channel_context = ChannelContext("telegram", "111")` y LLM payload `{"text": "msg"}` (sin `user_id`),
  cuando `_create` procesa `channel_send`,
  entonces `ChannelSendPayload.target == "telegram:111"`.

- **CS-4-B** — Dado `holder.channel_context = ChannelContext("telegram", "111")` y LLM payload `{"text": "msg", "user_id": "999"}`,
  cuando `_create` procesa `channel_send`,
  entonces `ChannelSendPayload.target == "telegram:999"` (override del LLM prevalece).

- **CS-4-C** — Dado `holder.channel_context = None` y LLM pide crear `channel_send`,
  cuando `_create` intenta procesar,
  entonces retorna `ToolResult(success=False, error=<mensaje indicando falta de contexto de canal>)`.

- **CS-4-D** — Dado cualquier `trigger_type != "channel_send"` (e.g. `agent_send`, `shell_exec`),
  cuando `_create` procesa,
  entonces el holder no es consultado y el comportamiento no cambia.

- **CS-4-E** — Dado una operación `update` con `trigger_payload` en una tarea `channel_send` existente,
  cuando `_update` procesa,
  entonces aplica la misma inyección de `target` que en `_create`.

---

### REQ-CS-5: Esquema LLM para channel_send

El `parameters_schema` de `SchedulerTool` se actualiza para reflejar la nueva interfaz:
- Se elimina `channel_id` de la descripción del `trigger_payload` para `channel_send`
- Se documenta `user_id` como campo opcional en el payload

**Esquema esperado en la descripción de `trigger_payload`:**
```
Para 'channel_send': {"text": "...", "user_id": "opcional — destinatario alternativo"}.
```

El LLM **nunca** ve ni provee el campo `target`. El campo `channel_id` desaparece completamente
del esquema y de la documentación expuesta al LLM.

**Scenarios:**

- **CS-5-A** — Dado el `parameters_schema` de `SchedulerTool`,
  cuando se inspecciona la propiedad `trigger_payload.description`,
  entonces no contiene la cadena `"channel_id"`.

- **CS-5-B** — Dado el `parameters_schema` de `SchedulerTool`,
  cuando se inspecciona la propiedad `trigger_payload.description`,
  entonces contiene documentación para `channel_send` con `text` y opcionalmente `user_id`.

---

### REQ-CS-6: Responsabilidades de los adaptadores inbound

Cada adaptador inbound es responsable de construir y registrar el `ChannelContext` correcto
antes de invocar `RunAgentUseCase.execute()`. La interfaz es `container.set_channel_context(ctx)`.

| Adaptador | `channel_type` | `user_id` | Fuente |
|-----------|---------------|-----------|--------|
| Telegram bot | `"telegram"` | `str(update.effective_user.id)` | `Update` de python-telegram-bot |
| CLI runner | `"cli"` | `"local"` | constante |
| REST router | `"rest"` | ID de request o `"anonymous"` | header / query param |
| Daemon runner | `"daemon"` | `"system"` | constante |

Restricciones:
- El adaptador llama `set_channel_context` **antes** de `execute()`
- `set_channel_context` en `AgentContainer` simplemente setea `holder.channel_context = ctx`
- Ningún adaptador construye el `target` directamente — eso es responsabilidad de `SchedulerTool`

**Scenarios:**

- **CS-6-A** — Dado que Telegram recibe un mensaje de `user_id=42`,
  cuando `_handle_message` procesa el mensaje,
  entonces llama `container.set_channel_context(ChannelContext(channel_type="telegram", user_id="42"))` antes de `run_agent.execute()`.

- **CS-6-B** — Dado que CLI recibe input del usuario,
  cuando `run_cli` invoca `run_agent.execute()`,
  entonces previamente llamó `container.set_channel_context(ChannelContext(channel_type="cli", user_id="local"))`.

- **CS-6-C** — Dado que el daemon runner activa un agente,
  cuando invoca `run_agent.execute()`,
  entonces previamente llamó `container.set_channel_context(ChannelContext(channel_type="daemon", user_id="system"))`.

- **CS-6-D** — Dado que el REST router recibe un request sin header de identificación,
  cuando invoca `run_agent.execute()`,
  entonces previamente llamó `container.set_channel_context(ChannelContext(channel_type="rest", user_id="anonymous"))`.

---

### REQ-CS-7: Reparación de ChannelSenderAdapter

`ChannelSenderAdapter.send_message` en `adapters/outbound/scheduler/dispatch_adapters.py`
actualmente referencia `self._container.telegram_gateway` que no existe en `AppContainer`.
Debe ser corregido para acceder al gateway de Telegram por la ruta correcta.

La corrección implica:
1. Identificar cómo `AppContainer` accede al bot de Telegram (o introducir un atributo `telegram_gateway` en `AppContainer`)
2. El gateway de Telegram debe ser accesible en `AppContainer` para que `ChannelSenderAdapter` pueda llamar `.send_message(int(user_id), text)`
3. `ChannelSenderAdapter.send_message` actualiza su firma para aceptar `target: str` (en lugar de `channel_id: str`) — el nombre del parámetro cambia, la lógica de parsing (`partition(":")`) permanece

Adicionalmente:
- Si Telegram no está configurado para ningún agente en el `AppContainer`, dispatch de `"telegram:..."` debe lanzar `ValueError` descriptivo
- Agregar soporte para `"cli:..."` y `"daemon:..."` como canales que no soportan dispatch programado (lanzar `NotImplementedError` o `ValueError` con mensaje claro)

**Scenarios:**

- **CS-7-A** — Dado `AppContainer` con un bot Telegram configurado y accesible como `app_container.telegram_gateway`,
  cuando `ChannelSenderAdapter.send_message("telegram:123", "hola")` es invocado,
  entonces llama `app_container.telegram_gateway.send_message(123, "hola")` sin error.

- **CS-7-B** — Dado `AppContainer` sin gateway Telegram (no configurado),
  cuando `ChannelSenderAdapter.send_message("telegram:123", "hola")` es invocado,
  entonces lanza `ValueError` con mensaje descriptivo (no `AttributeError`).

- **CS-7-C** — Dado `ChannelSenderAdapter.send_message("cli:local", "mensaje")`,
  cuando se invoca,
  entonces lanza `ValueError` indicando que el canal `"cli"` no soporta envío programado.

- **CS-7-D** — Dado `ChannelSenderAdapter.send_message("unknown:x", "texto")`,
  cuando se invoca,
  entonces lanza `ValueError("Unknown channel prefix: unknown")`.

---

### REQ-CS-8: Wiring en infrastructure/container.py

`AgentContainer` incorpora `ChannelContextHolder` y expone `set_channel_context()`.
`wire_scheduler` pasa el holder a `SchedulerTool`. `AppContainer` expone
`telegram_gateway` para que `ChannelSenderAdapter` pueda acceder al bot de Telegram.

**Cambios en AgentContainer:**
- Constructor crea `self._channel_context_holder = ChannelContextHolder()`
- Método público `set_channel_context(ctx: ChannelContext) -> None` setea `holder.channel_context = ctx`
- `wire_scheduler` pasa `channel_context_holder=self._channel_context_holder` a `SchedulerTool`

**Cambios en AppContainer:**
- Introduce `self.telegram_gateway: TelegramGateway | None` — referencia al bot Telegram
  (o wrapper que exponga `.send_message(user_id: int, text: str)`)
- Se inicializa durante el setup de Telegram (si el canal está configurado en algún agente)
- `ChannelSenderAdapter(self)` ya recibe `self` (AppContainer) — el fix es añadir el atributo

**Scenarios:**

- **CS-8-A** — Dado `AgentContainer` recién construido,
  cuando se inspecciona `container._channel_context_holder`,
  entonces existe y `holder.channel_context is None`.

- **CS-8-B** — Dado `container.set_channel_context(ChannelContext("telegram", "5"))`,
  cuando `SchedulerTool._create` lee el holder,
  entonces `holder.channel_context.routing_key == "telegram:5"`.

- **CS-8-C** — Dado `wire_scheduler(schedule_task_uc, user_timezone)`,
  cuando `SchedulerTool` es instanciado,
  entonces recibe `channel_context_holder` en su constructor.

- **CS-8-D** — Dado `AppContainer.__init__` con al menos un agente Telegram configurado,
  cuando se completa el init,
  entonces `app_container.telegram_gateway` es no-None y responde a `.send_message(int, str)`.
