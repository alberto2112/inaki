# Configuración — Iñaki v2

## Sistema de 4 capas de merge

La configuración final de cada agente se construye mergeando cuatro ficheros en orden.
Cada capa sobreescribe solo los campos que define — nunca elimina campos heredados ausentes.

```
config/global.yaml                 (1) config base del sistema
    ↓ merge campo a campo
config/global.secrets.yaml         (2) secrets globales (api keys compartidas)
    ↓ merge campo a campo
config/agents/{id}.yaml            (3) config del agente (canales, modelo, prompt)
    ↓ merge campo a campo
config/agents/{id}.secrets.yaml    (4) secrets del agente (tokens, auth keys)
    ↓
AgentConfig resuelto y completo
```

**Regla de secrets:** si un agente no define `llm.api_key`, hereda la del global.
Un secret ausente en nivel inferior nunca nullifica el del nivel superior.

**Arranque con secrets ausentes:** si `agents/{id}.secrets.yaml` no existe,
el sistema arranca con WARNING. Los canales que requieren secrets no levantan.
El CLI siempre funciona.

---

## Archivos de configuración

| Archivo | Commitable | Propósito |
|---------|-----------|-----------|
| `config/global.yaml` | ✅ sí | Config base del sistema (proveedor LLM, embeddings, memoria, paths) |
| `config/global.secrets.yaml` | ❌ no | API keys globales (llm.api_key) |
| `config/global.secrets.yaml.example` | ✅ sí | Referencia de qué secrets existen |
| `config/agents/{id}.yaml` | ✅ sí | Config del agente: id, name, description, system_prompt, overrides, channels |
| `config/agents/{id}.secrets.yaml` | ❌ no | Secrets del agente: tokens, auth_key |
| `config/agents/{id}.secrets.yaml.example` | ✅ sí | Referencia de secrets del agente |
| `config.yaml` (raíz) | ✅ sí | Referencia completa con todos los parámetros documentados |

`.gitignore` incluye: `config/*.secrets.yaml` y `config/agents/*.secrets.yaml`

---

## `config/global.yaml` — todos los campos

```yaml
app:
  name: "Iñaki"           # Nombre del sistema
  log_level: "INFO"       # DEBUG | INFO | WARNING | ERROR
  data_dir: "data"        # Directorio de datos en runtime
  models_dir: "models"    # Directorio de modelos ONNX
  default_agent: "general" # Agente usado por CLI sin --agent

llm:
  provider: "openrouter"  # openrouter | ollama | openai | groq
  base_url: "https://openrouter.ai/api/v1"
  model: "anthropic/claude-3-5-haiku"
  temperature: 0.7
  max_tokens: 2048
  # api_key → en global.secrets.yaml

embedding:
  provider: "e5_onnx"     # e5_onnx (local ONNX) | openai
  model_path: "models/e5-small"  # Ruta al directorio con model.onnx + tokenizer.json
  dimension: 384          # Dimensión del vector (384 para e5-small)

memory:
  db_path: "data/inaki.db"       # Base de datos SQLite con sqlite-vec
                                 # Memoria GLOBAL — compartida entre todos los agentes
  default_top_k: 5               # Número de recuerdos recuperados por RAG
  digest_size: 14                # Nº de recuerdos volcados al digest markdown
  digest_path: "~/.inaki/mem/last_memories.md"
                                 # Ruta al digest leído por el prompt builder en cada turno
  min_relevance_score: 0.5       # Umbral mínimo (0.0-1.0) para persistir un hecho extraído
                                 # por el LLM. Filtra ANTES de embedear (ahorra tokens).
  schedule: "0 3 * * *"          # Cron global: cuándo corre la consolidación nocturna.
                                 # Una única tarea que itera TODOS los agentes habilitados.
                                 # Reconciliada al arrancar el daemon: si cambia acá se
                                 # actualiza la fila en scheduler.db automáticamente.
  delay_seconds: 2               # Pausa (segundos) entre agente y agente durante la
                                 # consolidación global. Evita golpear rate-limits del
                                 # proveedor LLM cuando hay varios agentes habilitados.
  keep_last_messages: 0          # Mensajes por agente a preservar tras la consolidación.
                                 # Tras extraer los recuerdos al storage vectorial, el
                                 # resto del historial se trunca pero SE PRESERVAN los
                                 # últimos N mensajes como contexto inmediato para el
                                 # próximo turno. Sentinel: 0 → usar fallback del sistema (84).
                                 # Cualquier valor > 0 se respeta tal cual.

tools:
  rag_min_tools: 10              # Mínimo de tools registradas para activar RAG
  rag_top_k: 5                   # Nº máximo de tools seleccionadas por RAG
  rag_min_score: 0.0             # Score mínimo de cosine similarity (0.0-1.0)
                                 # para incluir una tool. 0.0 = sin filtro.
  tool_call_max_iterations: 5    # Máx. iteraciones del tool-loop por turno
  circuit_breaker_threshold: 2   # Fallos consecutivos antes de cortar el loop

skills:
  rag_min_skills: 10             # Mínimo de skills cargadas para activar RAG
  rag_top_k: 3                   # Nº máximo de skills seleccionadas por RAG
  rag_min_score: 0.0             # Score mínimo de cosine similarity (0.0-1.0)
                                 # para incluir una skill. 0.0 = sin filtro.

chat_history:
  db_path: "data/history.db"     # Base de datos SQLite del historial de conversación
                                 # (separada de inaki.db que usa sqlite-vec)
  max_messages: 21               # Últimos N mensajes inyectados al LLM (0 = sin límite)

scheduler:
  enabled: true                  # Arranca el SchedulerService en modo daemon
  db_path: "data/scheduler.db"   # Base de datos de tareas programadas
  max_retries: 3
  output_truncation_size: 65536

workspace:
  path: "~/inaki-workspace"      # Directorio raíz permitido para file tools (default global)
  containment: "strict"          # strict | warn | off
                                 # strict → bloquea paths fuera del workspace (recomendado)
                                 # warn   → permite pero loguea WARNING
                                 # off    → sin restricciones
                                 # Afecta a read_file, write_file, patch_file.
                                 # run_shell NO está sujeto a esta config.
                                 # Overrideable por agente en agents/{id}.yaml.

admin:
  host: "127.0.0.1"             # Interfaz de escucha del admin server (loopback = más seguro)
  port: 6497                    # Puerto del admin server
  chat_timeout: 300.0           # Timeout (segundos) para esperar respuesta del agente
                                # en POST /admin/chat/turn. Aumentar para modelos lentos.
  # auth_key → en global.secrets.yaml
```

### Admin server — endpoints expuestos

El admin server expone los siguientes endpoints bajo `http://{admin.host}:{admin.port}/`:

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/health` | Ping de salud (sin auth) |
| POST | `/inspect` | Inspect RAG para un agente (requiere X-Admin-Key) |
| POST | `/consolidate` | Consolidar memoria de agente(s) (requiere X-Admin-Key) |
| POST | `/scheduler/reload` | Recargar scheduler (requiere X-Admin-Key) |
| POST | `/admin/chat/turn` | Enviar un turno de chat al agente (requiere X-Admin-Key) |
| GET | `/admin/chat/history` | Obtener historial del agente (requiere X-Admin-Key) |
| DELETE | `/admin/chat/history` | Limpiar historial del agente (requiere X-Admin-Key) |
| GET | `/admin/agents` | Listar agentes registrados (requiere X-Admin-Key) |

#### POST `/admin/chat/turn`

```json
// Request body
{
  "agent_id": "dev",
  "session_id": "uuid-del-cliente-cli",
  "message": "Hola, ¿cómo estás?"
}

// Response 200
{
  "reply": "Estoy bien, ¿en qué te ayudo?",
  "agent_id": "dev",
  "session_id": "uuid-del-cliente-cli"
}
```

Errores posibles: `401` (sin X-Admin-Key), `404` (agent_id no registrado), `422` (body inválido), `500` (error interno del agente).

#### GET `/admin/chat/history?agent_id=dev`

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

#### DELETE `/admin/chat/history?agent_id=dev`

Retorna `204 No Content`. Borra el historial activo del agente (afecta a todos los canales — CLI, Telegram, etc.).

---

## `config/global.secrets.yaml`

```yaml
llm:
  api_key: "sk-or-..."    # API key del proveedor LLM global
```

---

## `config/agents/{id}.yaml` — estructura completa

```yaml
id: "general"                    # Identificador único del agente (= nombre del archivo)
name: "Iñaki-g"                  # Nombre para mostrar
description: "Asistente general" # Descripción breve
system_prompt: |                 # Prompt base del agente (requerido)
  Eres Iñaki, un asistente personal inteligente.
  Eres conciso, directo y útil.

# Overrides LLM — solo los campos que cambian, el resto se hereda del global
llm:
  model: "anthropic/claude-3-5-haiku"
  # provider, base_url, temperature, max_tokens → heredados del global

# Overrides de embedding (opcional)
# embedding:
#   provider: "e5_onnx"

# Memoria — ÚNICO flag válido per-agent
# El resto de memory.* se define en global.yaml y NO debe overridearse acá.
memory:
  enabled: true        # Si false, este agente NO entra en la consolidación
                       # nocturna global. Default: true.
                       # El comando `inaki consolidate --agent {id}` ignora
                       # este flag y consolida el agente indicado de todas formas.

# Workspace — contención de paths para file tools (read_file, write_file, patch_file)
# run_shell NO está afectado por esta config.
workspace:
  path: "/Users/alberto/tmp/mi_workspace"  # Directorio raíz permitido (default: cwd del proceso)
  containment: "strict"                    # strict | warn | off (default: strict)

# Canales disponibles para este agente
# Los valores sensibles (tokens, auth_key) van en {id}.secrets.yaml
channels:
  telegram:
    allowed_user_ids: ["123456789"]  # Lista vacía = todos permitidos
    reactions: true                  # Reaccionar con emojis a los mensajes
    debug: false
  rest:
    host: "0.0.0.0"
    port: 6498                       # Cada agente tiene su propio puerto
```

---

## `workspace` — contención de paths en file tools

Cada agente puede declarar un `workspace` para controlar qué paths pueden tocar
las tools de ficheros. Se configura en `config/agents/{id}.yaml`:

```yaml
workspace:
  path: "/Users/alberto/tmp/mi_workspace"  # Directorio raíz permitido
  containment: "strict"                    # strict | warn | off
```

**Modos de contención:**

| Modo | Comportamiento |
|------|----------------|
| `strict` | Bloquea cualquier path fuera de `workspace.path`. La tool devuelve error al LLM. **Default.** |
| `warn` | Permite paths fuera del workspace pero loguea un WARNING. Útil para debug. |
| `off` | Sin restricciones. La tool accede a cualquier path del sistema. |

**Tools afectadas por `workspace.containment`:**

| Tool | ¿Sandboxeada? |
|------|--------------|
| `read_file` | ✅ sí |
| `write_file` | ✅ sí |
| `patch_file` | ✅ sí |
| `run_shell` | ❌ no — ejecuta comandos sin restricción de paths |
| `delegate`, `scheduler`, resto de builtins | ❌ no aplica |

> **Nota:** `run_shell` es una extensión en `ext/` y no tiene contención de ningún tipo.
> Si el LLM puede llamar `run_shell`, puede operar en cualquier path del sistema.

Si `workspace.path` no se define en la config del agente, se usa el directorio de trabajo
del proceso al momento de arrancar. Para evitar ambigüedades en producción (systemd),
especificar siempre un path absoluto.

---

## `config/agents/{id}.secrets.yaml`

```yaml
channels:
  telegram:
    token: "7xxxxxxx:AAF..."     # Bot token de BotFather
  rest:
    auth_key: "sxc-0123456"      # Clave para header X-API-Key
# llm.api_key no definido aquí → hereda de global.secrets.yaml
```

---

## Reglas de merge por campo

| Campo | Comportamiento |
|-------|----------------|
| `llm` (bloque) | Merge campo a campo. Ausentes se heredan. |
| `llm.api_key` | Solo en secrets. Si ausente en agente → hereda del global. |
| `embedding` | Merge campo a campo si se define. |
| `memory.db_path` / `digest_path` / `default_top_k` / `min_relevance_score` / `schedule` / `delay_seconds` / `keep_last_messages` | **Solo en `global.yaml`**. Un agente no puede overridearlos (semánticamente no tiene sentido: la memoria es global compartida). |
| `memory.enabled` | **Solo per-agent en `agents/{id}.yaml`**. Default `true`. Filtra qué agentes participan en la consolidación nocturna global. |
| `channels` | Solo en el agente. No existe en global. |
| `channels.*.token` / `auth_key` | Solo en `*.secrets.yaml`. |
| `system_prompt` | Requerido en cada agente. Sin valor por defecto. |
| `id`, `name`, `description` | Requeridos en cada agente. |

---

## Resolución de paths

Los paths relativos en la config se resuelven desde el **directorio de trabajo** al momento
de arrancar (`cwd`). Para entornos productivos (systemd), especificar paths absolutos o
asegurarse de que `WorkingDirectory` en el unit file apunte al directorio del proyecto.

Ejemplo para Pi 5 en `config/global.yaml`:
```yaml
app:
  data_dir: "/home/pi/inaki/data"
  models_dir: "/home/pi/inaki/models"

embedding:
  model_path: "/home/pi/inaki/models/e5-small"

memory:
  db_path: "/home/pi/inaki/data/inaki.db"
  digest_path: "/home/pi/.inaki/mem/last_memories.md"

chat_history:
  db_path: "/home/pi/inaki/data/history.db"

scheduler:
  db_path: "/home/pi/inaki/data/scheduler.db"
```

---

## Añadir un nuevo agente

1. Crear `config/agents/miagente.yaml` con `id`, `name`, `description`, `system_prompt`
2. Crear `config/agents/miagente.secrets.yaml` con los tokens necesarios
3. Reiniciar el daemon: `systemctl restart inaki`

El `AgentRegistry` escanea automáticamente `config/agents/*.yaml` al arrancar.
No hay registro manual ni reinicio del código.

---

## Consolidación de memoria — configuración

La memoria a largo plazo se alimenta desde una única tarea programada global que
se dispara según `memory.schedule` (cron en `global.yaml`). Esa tarea itera todos
los agentes con `memory.enabled = true` y llama a cada uno en secuencia con una
pausa de `memory.delay_seconds` segundos entre ellos.

### Reconciliación al arrancar el daemon

Al iniciar, `AppContainer.startup()` reconcilia el estado de la tarea builtin
`consolidate_memory` (id=1) con la config:

| Situación | Acción |
|-----------|--------|
| La tarea no existe en `scheduler.db` | Se crea con el schedule de la config y `next_run` computado con croniter. |
| El `schedule` de la DB no coincide con el de la config | Se actualiza el schedule y se recomputa `next_run`. |
| La tarea está en `FAILED` (resto de corridas viejas) | Se resetea a `pending`, `retry_count=0` y se recomputa `next_run`. |
| `next_run` está en `NULL` | Se recomputa con croniter. |

Esto significa que **cambiar `memory.schedule` en `global.yaml` y reiniciar el
daemon basta** para aplicar el nuevo horario. No hay que editar `scheduler.db`
a mano.

### Trigger manual

| Comando | Efecto |
|---------|--------|
| `inaki consolidate` | Ejecuta el use case global — itera todos los agentes con `memory.enabled=true` respetando `delay_seconds`. |
| `inaki consolidate --agent dev` | Consolida solo `dev`, ignora el flag `enabled`. |

Ambos arrancan `AppContainer`, corren la consolidación one-shot e imprimen el
resultado por stdout. No arrancan el scheduler ni los canales.

### Filtro por relevance

El `ConsolidateMemoryUseCase` descarta los hechos extraídos por el LLM cuya
`relevance` sea menor a `memory.min_relevance_score`. El filtro se aplica
**antes** de generar embeddings, así que descartar ahorra llamadas al
embedder y storage en `inaki.db`.

### Retención del historial tras la consolidación

Tras una consolidación exitosa (extracción + persistencia de recuerdos OK),
el use case llama a `history.mark_infused(agent_id)` + `history.trim(agent_id,
keep_last=N)` donde `N` sale de `memory.keep_last_messages` con el sentinel
`0 → 84`. Esto significa:

- Los **últimos N mensajes** del agente quedan en `history.db` como contexto
  inmediato para el próximo turno (el prompt builder los inyecta normal).
- El **resto** se borra — los hechos relevantes ya están en la memoria
  vectorial (`inaki.db`) y los recuerdos recientes en `last_memories.md`.
- Los **N preservados** quedan marcados con `infused=1` para que la próxima
  consolidación **no los vuelva a procesar** (evita duplicados en la memoria
  vectorial por re-extracción).

**Transaccionalidad:** si cualquier paso falla (LLM, parseo, embedding,
persistencia, mark_infused), `trim` NO se llama. El historial queda intacto
y la próxima corrida reintenta el mismo contenido. No hay estado intermedio.

**Idempotencia:** ejecutar `/consolidate` dos veces seguidas es un no-op
la segunda vez: `load_uninfused` devuelve vacío y el use case retorna
"No hay mensajes nuevos para consolidar." sin tocar nada.

### Flag `infused` — gate contra reprocesamiento

La tabla `history` lleva una columna `infused INTEGER NOT NULL DEFAULT 0`:

- **`0`** — mensaje pendiente de extracción
- **`1`** — mensaje ya procesado por el extractor en una corrida previa

El flujo de consolidación es:

1. `load_uninfused(agent_id)` — SELECT sobre `WHERE infused = 0`
2. Si vacío → no-op (return early)
3. Extracción + persistencia (si falla en cualquier paso, no se toca el flag)
4. `mark_infused(agent_id)` — `UPDATE SET infused = 1 WHERE infused = 0`
5. `trim(agent_id, keep_last=N)` — DELETE all except last N (los N que
   quedan incluyen las filas marcadas en el paso 4)

`load()` y `load_full()` ignoran el flag — el prompt builder y `/history`
siempre ven el contexto completo, esté procesado o no.

**Migración automática:** DBs creadas antes de este cambio se migran en el
primer `_ensure_schema` vía `ALTER TABLE ADD COLUMN infused INTEGER NOT NULL
DEFAULT 0` seguido de `UPDATE history SET infused = 1` (se asume que las
filas preexistentes formaban parte de un estado estable).

`/clear` (slash command) sigue haciendo wipe total — es el mecanismo manual
para descartar el hilo. Separado de la consolidación.
