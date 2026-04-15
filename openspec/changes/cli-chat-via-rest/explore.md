# Exploración: cli-chat-via-rest

**Objetivo**: Migrar `inaki chat` para que hable al daemon via REST en lugar de hacer bootstrap local de `AppContainer` (que carga embeddings, memoria, extensiones).

**Strict TDD MODE**: activo para fases apply/verify. El agente que ejecute sdd-apply DEBE seguir strict-tdd.md.

---

## 1. Transport: SSE vs Turn-based long-polling vs WebSocket

### Opciones evaluadas

#### Opción A — SSE desde el admin server (recomendada)

El daemon expone `POST /admin/chat/stream` que devuelve `text/event-stream`. El `DaemonClient` lo consume con `httpx.stream()` (API sync de httpx), iterando líneas e imprimiendo tokens en tiempo real.

**Pros:**
- El per-agent REST ya tiene `/chat/stream` con SSE implementado y funcionando (`adapters/inbound/rest/routers/agents.py` líneas 51-94). La infraestructura de streaming SSE existe en el codebase.
- Máxima fidelidad UX: el usuario ve tokens apareciendo en tiempo real, igual que ChatGPT en la terminal.
- `httpx` sync soporta `with client.stream(...)` — no requiere async.
- Formato `data: <token>\n\n` + `data: [DONE]\n\n` es trivial de parsear.
- No hay estado de reconexión: cada turno es una request nueva, SSE es unidireccional.

**Contras:**
- El admin server todavía no tiene endpoint de chat — hay que añadirlo.
- El streaming SSE existente en per-agent REST está en el `AgentContainer`, el admin server opera sobre `AppContainer`. Hay que enrutar al container correcto.
- Si la conexión se corta a mitad del stream, el token parcial se pierde (sin recovery en CLI). Aceptable para el caso de uso.

**Riesgos:**
- `httpx.stream()` sync bloquea el hilo mientras lee — correcto para un CLI Typer que ya es single-thread.
- Raspbery Pi 5: SSE es una conexión HTTP/1.1 larga. Sin overhead extra. OK.

#### Opción B — Turn-based long-polling (JSON completo)

El CLI hace `POST /admin/chat` y espera la respuesta completa (sin streaming). Timeout extendido (~120s).

**Pros:**
- Más simple en el cliente: `response.json()` es suficiente.
- Compatible con la implementación existente de `DaemonClient._post()`.
- No requiere parseo de SSE.

**Contras:**
- UX pobre: el usuario ve el cursor parado hasta que termina la respuesta completa. Inaceptable para respuestas largas.
- El timeout debe ser muy generoso (agente con tools puede tardar minutos).
- No aprovecha el streaming LLM que ya existe en el daemon.

#### Opción C — WebSocket

**Pros:**
- Full-duplex bidireccional, permite múltiples turnos en una sola conexión.

**Contras:**
- `httpx` NO soporta WebSockets. Requeriría `websockets` lib o similar, rompiendo el estándar del proyecto (httpx sync).
- Complejidad desproporcionada para un CLI interactivo simple.
- FastAPI WebSocket tiene diferente model de auth (no headers fáciles).
- Descartado.

### Recomendación

**Opción A — SSE**. Reutiliza la infraestructura existente, ofrece streaming de tokens en tiempo real, y es compatible con httpx sync. El endpoint debe añadirse al admin server (no al per-agent REST) para que el CLI solo necesite conocer un puerto (6497).

**Open question para sdd-propose**: ¿El endpoint de chat va en el admin server (puerto 6497, un solo punto de entrada) o en el per-agent REST (puerto 6498+, el CLI conoce el puerto del agente)? Ver punto 4.

---

## 2. Session identification: UUID client-side vs server-assigned vs header X-Session-Id

### Contexto relevante

El `ChannelContext` es un value object inmutable con `channel_type` y `user_id`. Su `routing_key` es `"{channel_type}:{user_id}"`. El CLI actual lo setea como `ChannelContext(channel_type="cli", user_id="local")`. El historial se persiste por `agent_id` (no por session_id), lo que significa que hay un único historial por agente independientemente del canal.

### Opciones evaluadas

#### Opción A — UUID client-side (recomendada)

El CLI genera un UUID al arrancar (`session_id = str(uuid.uuid4())`). Lo envía en cada request como header `X-Session-Id`. El servidor lo usa como `user_id` en el `ChannelContext`.

**Pros:**
- Stateless en el servidor: no necesita registrar sesiones.
- Permite que el CLI se identifique de forma única aunque haya dos terminales corriendo.
- Compatible con el modelo actual de `ChannelContext`.
- Si el CLI se reinicia, puede reusar el mismo session_id (almacenado en una variable de entorno o generado nuevo — ambos válidos).
- Alineado con cómo Telegram usa el `user_id` nativo del usuario.

**Contras:**
- Si el usuario abre dos terminales con el mismo `--agent`, comparten historial (el historial es por `agent_id`). Eso ya sucede hoy — no es regresión.
- No hay resume de sesión tras restart del CLI (a menos que se persista el UUID).

#### Opción B — Server-assigned session

El servidor crea una sesión al recibir el primer mensaje, devuelve un `session_id`. El CLI lo guarda en memoria y lo reenvía en requests subsiguientes.

**Pros:**
- El servidor controla la identidad de sesión.

**Contras:**
- Requiere estado en el servidor (mapa session_id → ChannelContext).
- Complejidad innecesaria: el historial ya está en SQLite por `agent_id`, no hay nada más que persistir por sesión.
- Raspi 5: estado en memoria adicional sin beneficio claro.

#### Opción C — Sin session, siempre "local"

El CLI siempre envía `user_id: "local"` (como hace hoy).

**Pros:**
- Sin cambios en el protocolo.

**Contras:**
- Si dos CLI corren en paralelo, interleave de mensajes en el historial compartido.
- No escalable.

### Recomendación

**Opción A — UUID client-side**. El CLI genera un UUID por proceso al arrancar. Lo incluye en el body del request (`session_id` field) o como header `X-Session-Id`. El servidor lo mapea a `ChannelContext(channel_type="cli", user_id=session_id)`. Simple, stateless, y alineado con el modelo existente.

**Open question para sdd-propose**: ¿El `session_id` va como header HTTP (`X-Session-Id`) o como campo en el body del request? Header es más RESTful para identificación de cliente; body es más explícito.

---

## 3. `/clear` endpoint: diseño y alineación con el flujo Telegram

### Cómo funciona `/clear` hoy

En **CLI** (`cli_runner.py:77-79`):
```python
await container.run_agent._history.clear(agent_id)
print("Historial limpiado.")
```

En **Telegram** (`bot.py:75-84`): idéntico — llama directamente a `_history.clear(agent_cfg.id)`.

Ambos canales acceden directamente al `IHistoryStore` a través del `AgentContainer`. No existe una abstracción de "clear" en el use case — es una llamada directa al repo.

El `IHistoryStore.clear(agent_id)` borra todas las entradas del historial para ese agente, sin archivar. El historial es plano por `agent_id` — no hay segmentación por `user_id` ni `channel_type`.

### Opciones evaluadas

#### Opción A — `DELETE /admin/chat/history?agent_id=X` (recomendada)

Sigue la convención RESTful: `DELETE` para borrar un recurso. El per-agent REST ya tiene `DELETE /history` en `agents.py:120-126`.

El endpoint admin recibe `agent_id` (query param o body), obtiene el `AgentContainer` correspondiente del `AppContainer`, y llama a `_history.clear(agent_id)`.

**Pros:**
- Semánticamente correcto (HTTP DELETE).
- Consistente con el endpoint per-agent REST existente.
- Sin estado nuevo en el servidor.
- El `DaemonClient` puede añadir `clear_history(agent_id)` trivialmente.

**Contras:**
- Algunos proxies bloquean DELETE con body. Usar query param evita el problema.

#### Opción B — `POST /admin/chat/clear`

**Pros:**
- Evita cualquier problema con proxies y DELETE.
- Más explícito semánticamente para una "acción".

**Contras:**
- No sigue convenciones REST para operaciones sobre recursos.

#### Opción C — Incluir `/clear` como comando especial en el mismo endpoint de chat

El CLI envía `message: "/clear"` y el servidor lo interpreta como comando.

**Pros:**
- Un solo endpoint.

**Contras:**
- Mezcla concerns. El servidor tendría que parsear comandos CLI.
- Rompe el principio de responsabilidad única.
- Descartado.

### Nota sobre segmentación del historial

El historial actual es por `agent_id`, no por `user_id`. Esto significa que `/clear` borra TODA la historia del agente, sin importar qué terminal la generó. Esto es consistente con Telegram hoy (el bot comparte historial entre todos los usuarios del mismo agente). Si se quiere historial aislado por sesión CLI en el futuro, requeriría cambios en `IHistoryStore` — fuera del scope de esta feature.

### Recomendación

**Opción A — `DELETE /admin/chat/history`** con `agent_id` como query param. Alineado con el per-agent REST, semánticamente correcto, mínimo código nuevo.

---

## 4. Agent selection: `--agent` por request vs fijo en session creation

### Contexto

El CLI tiene `--agent` flag. Hoy el `DaemonClient` ya recibe `agent_id` para `inspect` y `consolidate`. El admin server accede a los `AgentContainer`s via `app_container.agents[agent_id]`.

### Opciones evaluadas

#### Opción A — `agent_id` por request (recomendada)

Cada request de chat incluye `agent_id` en el body. El servidor enruta al `AgentContainer` correcto.

**Pros:**
- Stateless en el servidor.
- Consistente con como `inspect` y `consolidate` ya funcionan en el admin.
- El CLI puede cambiar de agente en medio de la sesión (futuro: `/agent dev`).
- Sin estado de "sesión activa" en el servidor.

**Contras:**
- Leve overhead de routing en cada request (lookup en un dict — O(1), despreciable).

#### Opción B — Agente fijo en session creation

El CLI "registra" una sesión con un `agent_id` y luego solo envía mensajes.

**Pros:**
- Más limpio desde el punto de vista del protocolo de sesión.

**Contras:**
- Requiere estado en el servidor (mapa session → agent_id).
- Complejidad innecesaria dado que el historial es por `agent_id` de todas formas.
- Incompatible con el modelo stateless del admin server actual.

#### Opción C — Un endpoint por agente (via per-agent REST, puerto 6498+)

El CLI habla al puerto del agente directamente (6498 para el agente default).

**Pros:**
- Ya existe: `/chat/stream` está implementado en `agents.py`.
- Sin cambios en el admin server.

**Contras:**
- El CLI necesita conocer el puerto de cada agente, lo cual no está expuesto en la config de forma simple.
- El per-agent REST tiene su propio auth (`X-API-Key`), diferente del admin (`X-Admin-Key`). El DaemonClient tendría que manejar dos mecanismos de auth.
- Rompe la idea de "un único punto de entrada" para el CLI.

### Recomendación

**Opción A — `agent_id` por request**, enviado en el body JSON. El CLI lo toma del `--agent` flag (o `global_config.app.default_agent`) al arrancar y lo incluye en cada mensaje. El servidor hace `app_container.agents[agent_id]` — igual que `inspect` y `consolidate` ya hacen hoy.

---

## 5. History rendering: solo turno activo vs replay completo de HistoryRepo

### Contexto

El CLI actual (`cli_runner.py:67-75`) hace:
```python
history = await container.run_agent._history.load(agent_id)
```
`IHistoryStore.load()` carga el historial "activo" (no el full). El `load_full()` incluye mensajes infused/archivados.

### Opciones evaluadas

#### Opción A — GET /admin/chat/history?agent_id=X (recomendada)

El daemon expone el historial via HTTP. El `DaemonClient` lo consume. El CLI lo renderiza.

**Pros:**
- Consistente con el per-agent REST que ya tiene `GET /history`.
- El historial vive en el daemon (SQLite) — es la fuente de verdad.
- No requiere estado local en el CLI.

**Contras:**
- Una request HTTP adicional para `/history`. Aceptable (operación bajo demanda con `/history` comando).

#### Opción B — El CLI mantiene historial local (espejo)

El CLI guarda una copia local de los turnos que él mismo genera.

**Pros:**
- Sin request adicional para mostrar el historial de la sesión actual.

**Contras:**
- Inconsistente: si el agente tiene historial previo (Telegram, otra sesión CLI), el espejo local no lo ve.
- Estado duplicado. El historial canónico siempre está en el daemon.
- Complejidad sin beneficio real.

#### Opción C — Solo mostrar turnos de la sesión actual (en memoria)

El CLI mantiene en una lista los pares (user, assistant) de la sesión actual.

**Pros:**
- Muy simple.
- No hay round-trip al servidor.

**Contras:**
- `/history` mostraría solo la sesión actual, no el historial completo. Comportamiento diferente al actual.
- Consistencia rota con Telegram (que muestra todo).

### Qué mostrar: load() vs load_full()

El comportamiento actual del CLI usa `_history.load()` (activo, sin infused). Recomendado mantener eso para consistencia. El endpoint `GET /admin/chat/history` debería devolver lo mismo que `load()`, no `load_full()`.

### Recomendación

**Opción A — GET /admin/chat/history?agent_id=X**. El daemon es la fuente de verdad. El CLI hace una request HTTP solo cuando el usuario escribe `/history`. Simple, consistente con el per-agent REST existente.

---

## Resumen de recomendaciones

| Punto | Recomendación |
|-------|--------------|
| 1. Transport | SSE via `httpx.stream()` sync |
| 2. Session ID | UUID client-side, enviado por request |
| 3. /clear | `DELETE /admin/chat/history?agent_id=X` |
| 4. Agent selection | `agent_id` por request (en body JSON) |
| 5. History rendering | `GET /admin/chat/history?agent_id=X` |

## Endpoints nuevos en admin server (todos con auth X-Admin-Key)

```
POST   /admin/chat/stream     → SSE streaming de un turno
GET    /admin/chat/history    → historial activo del agente
DELETE /admin/chat/history    → clear del historial
```

## Open questions para sdd-propose

1. ¿`session_id` como header `X-Session-Id` o campo en el body?
2. ¿Endpoint de chat en admin (6497) o per-agent REST (6498+)? La recomendación es admin (un solo punto de entrada), pero tiene implicancias para el routing dentro del AppContainer.
3. ¿`/history` y `/clear` deben ir en admin o se puede reusar el per-agent REST (implicaría que el DaemonClient conoce el puerto del agente)?
4. ¿Se mantiene compatibilidad con los comandos `/consolidate`, `/inspect` del CLI via REST también, o solo `/clear` e `/history`?
5. ¿El streaming en admin replica el streaming del per-agent REST (que bypassa la tool loop) o se integra con `run_agent.execute()` completo incluyendo tools?

**Nota crítica sobre el streaming del per-agent REST**: el endpoint `/chat/stream` en `agents.py` (líneas 64-93) NO usa `run_agent.execute()` — hace el pipeline RAG manualmente y solo llama a `_llm.stream()`, saltando la tool loop. Si la migración del CLI requiere tool calls, el endpoint de admin chat DEBE usar `run_agent.execute()` (que incluye la tool loop) y el streaming de tokens requeriría refactor del use case para emitir tokens incrementalmente. Esto es un riesgo/complejidad importante para la propuesta.
