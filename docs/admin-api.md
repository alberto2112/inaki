# Admin API — superficie HTTP del daemon

Referencia de los endpoints que expone el admin server en
`http://{admin.host}:{admin.port}/`. Los campos de config (`admin.host`,
`admin.port`, `admin.auth_key`) están en [`config-reference.md`](config-reference.md).

Todos los endpoints salvo `/health` exigen la cabecera `X-Admin-Key`. Esta es la
**única** superficie HTTP del daemon — el ruteo es por `agent_id`, no hay un
servidor REST por agente.

## Endpoints

| Método | Path | Descripción |
|--------|------|-------------|
| GET | `/health` | Ping de salud (sin auth) |
| POST | `/inspect` | Inspecciona el pipeline de prompt de un agente |
| POST | `/consolidate` | Consolida memoria — body `{"agent_id": "X"}` para uno, vacío para todos |
| POST | `/scheduler/reload` | Recarga el scheduler |
| POST | `/scheduler/run` | Dispara una tarea ahora — body `{"task_id": N}`. Corrida de prueba no destructiva: dispara el trigger una vez sin tocar `status`/`next_run`/`executions_remaining`. `404` si la tarea no existe. Cliente: `inaki scheduler run <ID>` |
| POST | `/admin/reload` | Hot-reload del daemon (cierra canales, recarga config, reinicia) |
| GET | `/admin/agents` | Lista los ids de agente registrados |
| GET | `/admin/agent/info` | Metadata del agente (`?agent_id=X` → id, name, description) |
| POST | `/admin/chat/turn` | Manda un turno de chat a un agente |
| POST | `/admin/chat/task` | Tarea efímera oneshot (carga historial, no persiste) |
| GET | `/admin/chat/history` | Devuelve el historial del agente |
| DELETE | `/admin/chat/history` | Borra el historial del agente |
| GET | `/admin/tool/list` | Lista las tools registradas en un agente |
| POST | `/admin/tool/invoke` | Invoca una tool directamente |
| POST | `/admin/send` | Manda texto/media a un canal desde un agente |

> `POST /admin/tool/invoke` es el **gateway admin único** de la regla del canal
> THIN: una capacidad se implementa una vez y se expone por use case, tool del
> LLM y este gateway. Ver [`arquitectura.md`](arquitectura.md).

## POST `/admin/chat/turn`

```json
// Request body
{
  "agent_id": "dev",
  "session_id": "uuid-del-cliente-cli",
  "message": "Hola, ¿cómo estás?",
  "channel": "telegram",      // opcional — declara el channel_type del turno
  "chat_id": "-1001234"       // opcional — ambos o ninguno, junto a channel
}

// Response 200
{
  "reply": "Estoy bien, ¿en qué te ayudo?",
  "agent_id": "dev",
  "session_id": "uuid-del-cliente-cli"
}
```

`channel` + `chat_id` son opcionales y viajan juntos (ambos o ninguno, `422` si
no). Cuando están, el `ChannelContext` usa ese `channel_type` y el turno opera
sobre el scope real de historial `(agent_id, channel, chat_id)` — útil para
simular un turno como si viniera de otro canal. Cuando se omiten, se usa
`channel_type="cli"` y el scope compartido legacy `("", "")`.

Errores posibles: `401` (falta X-Admin-Key), `404` (agent_id no registrado),
`422` (body inválido), `500` (error interno del agente).

## POST `/consolidate`

Con `{"agent_id": "dev"}` consolida solo ese agente (`404` si el agente no
existe, `503` si tiene `memories.consolidation.enabled=false`). Con body vacío
(o sin `agent_id`) consolida todos.

## GET `/admin/chat/history?agent_id=dev`

```json
// Response 200
{
  "agent_id": "dev",
  "messages": [
    {"role": "user", "content": "Hola", "timestamp": "2026-01-01T12:00:00"},
    {"role": "assistant", "content": "¡Hola!", "timestamp": "2026-01-01T12:00:01"}
  ]
}
```

## DELETE `/admin/chat/history?agent_id=dev`

Devuelve `204 No Content`. Borra el historial activo del agente (afecta a todos
los canales — CLI, Telegram, etc.).
