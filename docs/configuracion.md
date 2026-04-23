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
| `config/global.secrets.yaml` | ❌ no | Registro de credenciales (`providers.<name>.api_key`) |
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
  default_agent: "general" # Agente usado por CLI sin --agent

# Registro top-level de proveedores externos. Centraliza api_key + base_url
# por vendor. Las features (llm, embedding, transcription, memory.llm) solo
# referencian por nombre — NO llevan api_key/base_url propios.
providers:
  openrouter:
    # type: openrouter      # opcional — default = la key ("openrouter")
    api_key: "sk-or-..."    # → global.secrets.yaml
    base_url: "https://openrouter.ai/api/v1"
  openai:
    api_key: "sk-..."
  groq:
    api_key: "gsk_..."
    base_url: "https://api.groq.com/openai/v1"
  ollama:
    # type: ollama — provider LOCAL, no requiere api_key.
    # La entrada entera es opcional; si no existe, se usa el default del adapter.
    base_url: "http://localhost:11434"
  # Multi-instancia: dos cuentas del mismo vendor (p. ej. billing mixto)
  # groq-work:
  #   type: groq            # apunta al adapter "groq"
  #   api_key: "gsk_work_..."

llm:
  provider: "openrouter"  # referencia a providers.openrouter
  model: "anthropic/claude-3-5-haiku"
  temperature: 0.7
  max_tokens: 2048

embedding:
  provider: "e5_onnx"     # e5_onnx (local ONNX, no requiere api_key) | openai
  model_dirname: "models/e5-small"  # Dir con model.onnx + tokenizer.json (relativo a ~/.inaki/)
  dimension: 384          # Dimensión del vector (384 para e5-small)

memory:
  db_filename: "data/inaki.db"  # Fichero SQLite con sqlite-vec (relativo a ~/.inaki/)
                                 # Memoria GLOBAL — compartida entre todos los agentes
  default_top_k: 5               # Número de recuerdos recuperados por búsqueda vectorial
  digest_size: 14                # Nº de recuerdos volcados al digest markdown
  digest_filename: "mem/last_memories.md"
                                 # Digest leído por el prompt builder (relativo a ~/.inaki/)
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
  semantic_routing_min_tools: 10  # Mínimo de tools registradas para activar semantic routing
  semantic_routing_top_k: 5       # Nº máximo de tools seleccionadas por routing
  semantic_routing_min_score: 0.0 # Score mínimo de cosine similarity (0.0-1.0)
                                  # para incluir una tool. 0.0 = sin filtro.
  tool_call_max_iterations: 5     # Máx. iteraciones del tool-loop por turno
  circuit_breaker_threshold: 2    # Fallos consecutivos antes de cortar el loop

skills:
  semantic_routing_min_skills: 10  # Mínimo de skills cargadas para activar routing
  semantic_routing_top_k: 3        # Nº máximo de skills seleccionadas por routing
  semantic_routing_min_score: 0.0  # Score mínimo de cosine similarity (0.0-1.0)
                                   # para incluir una skill. 0.0 = sin filtro.

chat_history:
  db_filename: "data/history.db"  # Fichero SQLite del historial (relativo a ~/.inaki/)
                                 # separado de inaki.db (que usa sqlite-vec)
  max_messages: 21               # Últimos N mensajes inyectados al LLM (0 = sin límite)

scheduler:
  enabled: true                  # Arranca el SchedulerService en modo daemon
  db_filename: "data/scheduler.db"  # Fichero SQLite de tareas programadas (relativo a ~/.inaki/)
  max_retries: 3
  output_truncation_size: 65536
  channel_fallback:              # Cascada de resolución para dispatch de canales (ver abajo)
    default: null                # str|null — sink por defecto si no hay override ni nativo
    overrides: {}                # dict[channel_type, target] — override por canal origen

workspace:
  path: "~/inaki-workspace"      # Directorio raíz permitido para file tools (default global)
  containment: "strict"          # strict | warn | off
                                 # strict → bloquea paths fuera del workspace (recomendado)
                                 # warn   → permite pero loguea WARNING
                                 # off    → sin restricciones
                                 # Afecta a read_file, write_file, patch_file.
                                 # shell_exec NO está sujeto a esta config.
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
| POST | `/inspect` | Inspect del pipeline de prompt para un agente (requiere X-Admin-Key) |
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

## `knowledge:` — Fuentes de conocimiento externas

La sección `knowledge:` vive **solo en `global.yaml`** — no se puede configurar por agente.
Controla el pipeline de recuperación de conocimiento externo (RAG) que se ejecuta antes de cada turno.

```yaml
knowledge:
  enabled: true                    # Si false, el pre-fetch se saltea completamente.
                                   # Default: true.

  include_memory: true             # Si true, la memoria SQLite del agente se registra
                                   # automáticamente como fuente "memory".
                                   # Default: true.

  top_k_per_source: 3              # Resultados máximos por fuente (default global).

  min_score: 0.5                   # Score mínimo de coseno para incluir un fragmento.
                                   # Rango: 0.0-1.0. Default: 0.5.

  max_total_chunks: 10             # Cap total de fragmentos tras el fan-out a todas
                                   # las fuentes (ordenados por score desc, se trunca).

  token_budget_warn_threshold: 4000
                                   # Si el estimado de tokens totales
                                   # (chunks + digest + skills) supera este valor,
                                   # se emite un WARNING con el desglose.
                                   # Heurística: len(texto) / 4.
                                   # 0 = warning deshabilitado.

  sources:
    - id: docs-proyecto            # ID único de la fuente (usado en CLI y rutas de DB)
      type: document               # "document" = carpeta de archivos
      enabled: true                # Si false, la fuente se ignora al arrancar.
      description: "Project docs"  # Descripción inyectada en el system prompt
      path: ~/proyecto/docs/       # Carpeta a indexar (soporta ~). Requerido.
      glob: "**/*.md"              # Glob pattern para seleccionar archivos.
                                   # Ejemplos: "**/*.md", "**/*.{md,txt,pdf}"
      chunk_size: 500              # Tamaño de cada chunk en palabras.
      chunk_overlap: 80            # Solapamiento entre chunks consecutivos (en palabras).
      top_k: 3                     # Resultados máximos de esta fuente.
      min_score: 0.5               # Score mínimo de esta fuente (override del global).

    - id: mi-base                  # ID único de la fuente
      type: sqlite                 # "sqlite" = DB pre-construida por el usuario
      enabled: true
      description: "My knowledge base"
      path: ~/data/knowledge.db    # Path a la DB SQLite del usuario. Requerido.
      top_k: 3
      min_score: 0.5
```

#### Fuente `type: sqlite` — Base de datos pre-construida por el usuario

Permite conectar una base de datos SQLite que el usuario construyó y gestiona por su cuenta.
Iñaki **no indexa ni escribe** esta DB — solo la consulta para búsquedas vectoriales.

**Schema requerido:**

```sql
-- Tabla de texto y metadatos (id debe ser la PRIMARY KEY entera)
CREATE TABLE chunks (
    id            INTEGER PRIMARY KEY,
    source_path   TEXT NOT NULL,
    content       TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}'
);

-- Tabla virtual vec0 con embeddings de 384 dimensiones (e5-small)
-- El rowid de chunk_embeddings debe coincidir con chunks.id
CREATE VIRTUAL TABLE chunk_embeddings USING vec0(embedding FLOAT[384]);
```

**Notas importantes:**

- La dimensión **debe ser exactamente 384** — es la dimensión del modelo e5-small que usa Iñaki internamente. Si la DB usa otra dimensión, la fuente se omite al arrancar con un error claro en los logs.
- `chunk_embeddings.rowid` se usa para el JOIN con `chunks.id` — deben coincidir.
- `metadata_json` es opcional pero debe ser JSON válido si está presente (o `NULL`/`'{}'`).
- Iñaki valida el schema en la primera búsqueda. Si la validación falla, la fuente se deshabilita para esa sesión y se loguea `ERROR` con el nombre de la fuente y el motivo exacto.

**Ejemplo mínimo de inserción:**

```python
import sqlite3, struct, numpy as np

conn = sqlite3.connect("knowledge.db")
conn.enable_load_extension(True)
conn.load_extension("vec0")  # sqlite-vec

conn.execute("""
    CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY, source_path TEXT NOT NULL,
        content TEXT NOT NULL, metadata_json TEXT DEFAULT '{}'
    )
""")
conn.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(embedding FLOAT[384])
""")

content = "Texto del chunk a indexar"
embedding = np.random.randn(384).astype(np.float32)  # reemplazar por tu embedder real
vec_bytes = struct.pack("384f", *embedding)

conn.execute("INSERT INTO chunks (source_path, content) VALUES (?, ?)", ("/ruta/doc.md", content))
row_id = conn.lastrowid
conn.execute("INSERT INTO chunk_embeddings (rowid, embedding) VALUES (?, ?)", (row_id, vec_bytes))
conn.commit()
```

### Indexación de documentos

Los documentos se indexan offline con el comando CLI:

```bash
inaki knowledge index docs-proyecto   # Indexa o re-indexa la fuente
inaki knowledge list                   # Lista fuentes configuradas
inaki knowledge stats docs-proyecto    # Estadísticas del índice
```

La indexación es **incremental**: solo se re-procesan los archivos cuya `mtime` cambió
desde la última indexación. Los embeddings se persisten en `~/.inaki/knowledge/{id}.db`.

### Formatos soportados

| Formato | Estrategia de chunking |
|---------|------------------------|
| `.md`   | Split por headers (`#`/`##`/`###`), ventana deslizante dentro de cada sección |
| `.txt`  | Ventana deslizante pura |
| `.pdf`  | Extracción página a página con `pypdf`, ventana deslizante sobre el texto total |
| otros   | Ventana deslizante pura (texto plano) |

### Schema de la DB de índice (`~/.inaki/knowledge/{id}.db`)

```sql
CREATE TABLE chunks (
    id          TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    file_mtime  REAL NOT NULL,
    chunk_idx   INTEGER NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE VIRTUAL TABLE chunk_embeddings USING vec0(
    id        TEXT PRIMARY KEY,
    embedding FLOAT[384]
);
CREATE TABLE files_indexed (
    file_path   TEXT PRIMARY KEY,
    mtime       REAL NOT NULL,
    chunk_count INTEGER NOT NULL
);
```

---

## `config/global.secrets.yaml`

El registro de credenciales vive bajo `providers:` y se mergea con el `providers:`
de `global.yaml` (deep-merge campo a campo).

```yaml
providers:
  openrouter:
    api_key: "sk-or-..."
  openai:
    api_key: "sk-..."
  groq:
    api_key: "gsk_..."
```

Una entrada declarada en `global.yaml` (p. ej. con `base_url`) se completa con
la `api_key` de este archivo — no hace falta repetir campos.

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
# shell_exec NO está afectado por esta config.
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
    voice_enabled: true              # Acepta voz/audio/video_note (default: true)
                                     # Requiere bloque [transcription] resuelto
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
| `shell_exec` | ❌ no — ejecuta comandos sin restricción de paths |
| `delegate`, `scheduler`, resto de builtins | ❌ no aplica |

> **Nota:** `shell_exec` es una extensión en `ext/` y no tiene contención de ningún tipo.
> Si el LLM puede llamar `shell_exec`, puede operar en cualquier path del sistema.

Si `workspace.path` no se define en la config del agente, se usa el directorio de trabajo
del proceso al momento de arrancar. Para evitar ambigüedades en producción (systemd),
especificar siempre un path absoluto.

---

## `transcription` — transcripción de voz (Telegram)

Habilita la transcripción de mensajes de voz, audio y video_note en Telegram.
Se define en `config/global.yaml` (o sobrescribible per-agent) y se activa con
`channels.telegram.voice_enabled: true` (default).

```yaml
transcription:
  provider: "groq"                     # referencia a providers.groq
  model: "whisper-large-v3-turbo"
  language: "es"                        # ISO-639-1; null = autodetect
  timeout_seconds: 60
  max_audio_mb: 25                      # Límite de Groq; audios mayores se rechazan sin llamar al provider
```

Las credenciales (`api_key`, `base_url`) NO van en este bloque — se resuelven
desde `providers.groq` en el registro.

**Feature flag en el agente:**

```yaml
channels:
  telegram:
    voice_enabled: true   # default — acepta voz/audio/video_note
    # voice_enabled: false — drop silencioso, el bot ignora audios
```

**Flujo del handler de voz:**

1. Usuario autorizado (`allowed_user_ids`) — sino, drop silencioso.
2. `voice_enabled: true` — sino, drop silencioso.
3. Tamaño ≤ `max_audio_mb` — sino, reacción ❌ + reply con el tamaño.
4. Reacción 👂 al inicio.
5. Transcripción → mismo pipeline que un mensaje de texto (reply HTML + ✅/❌).

**Errores comunes al arrancar:**

- Agente con `voice_enabled: true` y sin bloque `transcription:` resuelto
  → falla en el bootstrap con un error claro pidiendo añadir `transcription:`
  o poner `voice_enabled: false`.
- `providers.<provider>.api_key` ausente para el provider referenciado por
  `transcription.provider` → `ConfigError` al arranque (fail-fast, antes
  de instanciar adapters).

> ⚠ **Privacidad:** el audio se envía al proveedor externo (hoy: Groq). Para
> contenido sensible poné `voice_enabled: false` en ese agente o esperá a que
> exista un proveedor local. La app NO persiste el audio; sí queda el texto
> transcripto en el chat_history y puede alimentar la memoria.

---

## `broadcast` — canal de difusión entre instancias de Iñaki

Permite que dos o más instancias de Iñaki (p. ej. una en cada Raspberry Pi)
compartan el contexto conversacional de un grupo de Telegram en tiempo real.
Una instancia actúa como **servidor** (escucha conexiones) y el resto como
**clientes** (se conectan al servidor). Topología en estrella: un servidor, N clientes.

### Bloques de config

**`allowed_chat_ids`** — grupos autorizados (se suma a la config existente del canal):

```yaml
channels:
  telegram:
    api_key: "..."
    allowed_user_ids: [12345]
    allowed_chat_ids: [-1001234567890]  # lista de grupos permitidos; enteros negativos
```

Si `allowed_chat_ids` está vacío o ausente, solo se admiten chats privados de usuarios en
`allowed_user_ids`. Para habilitar grupos hay que listar sus chat_ids explícitamente.

---

**`channels.telegram.broadcast`** — modo servidor (esta instancia escucha conexiones entrantes):

```yaml
channels:
  telegram:
    api_key: "..."
    broadcast:
      port: 1234                          # puerto TCP de escucha (1024..65535)
      auth: "shared-secret-entre-agentes" # secreto compartido HMAC-SHA256
      bot_username: "inaki_a_bot"         # username del bot sin @, para detección de menciones
      behavior: mention                   # listen | mention | autonomous
      rate_limiter: 5                     # máx. respuestas proactivas por ventana de 30s por chat
```

---

**`channels.telegram.broadcast`** — modo cliente (esta instancia conecta al servidor):

```yaml
channels:
  telegram:
    api_key: "..."
    broadcast:
      remote:
        host: "192.168.1.10:1234"           # ip:port del servidor
        auth: "shared-secret-entre-agentes" # debe coincidir con el servidor
      bot_username: "inaki_b_bot"
      behavior: autonomous
      rate_limiter: 5
```

---

**`memory.channels_infused`** — limitar qué canales alimentan la consolidación de memoria:

```yaml
memory:
  channels_infused: ["telegram"]  # null o ausente = todos los canales se consolidan
```

Útil cuando tenés un agente activo en CLI y Telegram pero solo querés que las
conversaciones de Telegram entren en la memoria a largo plazo.

---

### Modos de comportamiento (`behavior`)

| Modo | Descripción |
|------|-------------|
| `listen` | El bot nunca responde. Solo absorbe contexto en el buffer de broadcast. Útil para un agente "observador". |
| `mention` | El bot responde solo cuando alguien lo menciona con `@bot_username`. **Default en grupos.** |
| `autonomous` | El LLM decide si responder. Si no tiene nada útil que aportar, responde `[SKIP]` internamente y el sistema no envía nada al grupo. Además, el bot dispara su pipeline ante **cualquier mensaje broadcast** (bot-to-bot): el user_input se inyecta con un prefijo `[<origen>]` y el LLM decide si responder o emitir `[SKIP]`. Permite que dos bots dialoguen entre sí en un grupo. |

El **rate limiter** (`rate_limiter: 5`) aplica en modo `autonomous` para ambas vías:
mensajes entrantes de Telegram **y** triggers broadcast bot-to-bot. Limita las respuestas
proactivas a N mensajes por ventana fija de 30 segundos, por combinación `(agente, chat_id)`.
Cuando se alcanza el límite, los mensajes siguientes se descartan hasta que la ventana se resetea.

---

### Obtener el `chat_id` de un grupo — bootstrap con `/chatid`

Para autorizar un grupo en `allowed_chat_ids` necesitás saber su `chat_id` numérico.
Las interfaces de Telegram no lo muestran. El flujo de bootstrap es:

1. Agregá el bot al grupo como administrador.
2. Desde tu cuenta (que ya está en `allowed_user_ids`), enviá el mensaje `/chatid` en
   el grupo.
3. El bot responde con el `chat_id` numérico del grupo (p. ej. `-1001234567890`).
4. Copiá ese número en `allowed_chat_ids` de la config del agente.
5. Reiniciá el daemon: `systemctl restart inaki`.

**¿Por qué `/chatid` no requiere `allowed_chat_ids`?** Precisamente para resolver el
huevo y la gallina: el grupo no puede estar en la whitelist si todavía no sabés su id.
Por eso el comando saltea la validación de `allowed_chat_ids`.

El comando **sí respeta `allowed_user_ids`** — solo usuarios autorizados pueden consultarlo.
Un atacante que logre poner al bot en un grupo desconocido no puede hacer nada útil
solo con el chat_id.

---

### Requisito de NTP — sincronización de relojes

El canal de broadcast usa **HMAC-SHA256** con una ventana de frescura de **60 segundos**.
Al validar un mensaje entrante, el receptor calcula `|now − timestamp_mensaje| > 60s` y si
es verdadero lo descarta silenciosamente.

**Ambas Raspberry Pi (o cualquier par de agentes) deben tener el reloj sincronizado
por NTP.** El cliente NTP por defecto de Raspberry Pi OS (`systemd-timesyncd` o `chrony`)
es suficiente. No requiere configuración adicional si el Pi tiene acceso a internet.

**Modo de falla:** si los relojes derivan más de ~60 segundos entre sí, **todos los
mensajes de broadcast se descartan** sin ningún aviso visible al usuario. El único
indicio son las entradas de log con el evento `broadcast.message.dropped.stale_timestamp`.
Esta condición es operativamente invisible si no se monitorean los logs, por eso el
requisito es crítico.

Para verificar que NTP está activo:
```bash
timedatectl status          # ver "NTP service: active"
systemctl status systemd-timesyncd  # o chrony
```

---

## `config/agents/{id}.secrets.yaml`

```yaml
channels:
  telegram:
    token: "7xxxxxxx:AAF..."     # Bot token de BotFather
  rest:
    auth_key: "sxc-0123456"      # Clave para header X-API-Key

# providers no definido aquí → hereda de global + global.secrets.
# Si el agente necesita una api_key distinta (p. ej. otra cuenta de Groq):
# providers:
#   groq:
#     api_key: "gsk_agent_specific_..."
```

---

## Reglas de merge por campo

| Campo | Comportamiento |
|-------|----------------|
| `llm` (bloque) | Merge campo a campo. Ausentes se heredan. Sin `api_key`/`base_url` (viven en `providers`). |
| `providers` (bloque) | Merge campo a campo por key. Una capa inferior puede completar una entrada declarada arriba. |
| `providers.<name>.api_key` | Solo en `*.secrets.yaml`. Un agente puede redefinir un provider entero. |
| `embedding` | Merge campo a campo si se define. Sin `api_key`/`base_url`. |
| `transcription` (bloque) | Merge campo a campo. Sin `api_key`/`base_url` (viven en `providers`). |
| `channels.telegram.voice_enabled` | Per-agent. Default `true`. Si `true` requiere bloque `transcription:`. |
| `memory.db_filename` / `digest_filename` / `default_top_k` / `min_relevance_score` / `schedule` / `delay_seconds` / `keep_last_messages` | **Solo en `global.yaml`**. Un agente no puede overridearlos (semánticamente no tiene sentido: la memoria es global compartida). |
| `memory.enabled` | **Solo per-agent en `agents/{id}.yaml`**. Default `true`. Filtra qué agentes participan en la consolidación nocturna global. |
| `channels` | Solo en el agente. No existe en global. |
| `channels.*.token` / `auth_key` | Solo en `*.secrets.yaml`. |
| `system_prompt` | Requerido en cada agente. Sin valor por defecto. |
| `id`, `name`, `description` | Requeridos en cada agente. |

---

## Resolución de paths

Los campos de path de runtime (`*_filename`, `*_dirname`) se resuelven así:

- **Paths relativos** (p. ej. `"data/inaki.db"`) se anclan bajo `~/.inaki/`.
- **Paths absolutos** (p. ej. `"/srv/inaki/data/inaki.db"`) se usan tal cual.
- **Tildes** (`~/...`) se expanden al home del usuario.
- El valor SQLite especial `:memory:` pasa sin interpretarse como path.

La raíz `~/.inaki/` está fija — es la misma que usan config/agents/secrets —
siguiendo el principio de separación entre datos de usuario y árbol del proyecto.

Layout por defecto:
```
~/.inaki/
├── config/            # YAMLs de global + secrets
├── agents/            # YAMLs por agente + secrets
├── data/              # DBs SQLite (inaki.db, history.db, scheduler.db, embedding_cache.db)
├── models/            # Modelos ONNX (e.g. e5-small/)
├── mem/               # Digest markdown (last_memories.md)
├── ext/               # Extensiones del usuario
└── .env               # INAKI_SECRET_KEY
```

Si necesitás mover el storage a otra raíz (p. ej. disco dedicado en Pi 5), pasá
paths absolutos en `~/.inaki/config/global.yaml`:
```yaml
embedding:
  model_dirname: "/srv/inaki/models/e5-small"
  cache_filename: "/srv/inaki/data/embedding_cache.db"

memory:
  db_filename: "/srv/inaki/data/inaki.db"
  digest_filename: "/srv/inaki/mem/last_memories.md"

chat_history:
  db_filename: "/srv/inaki/data/history.db"

scheduler:
  db_filename: "/srv/inaki/data/scheduler.db"
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

### LLM dedicado para consolidación — `memory.llm`

Por defecto, el `ConsolidateMemoryUseCase` usa el mismo `ILLMProvider` que el
agente (`llm.*`). Esto es conveniente, pero tiene un pitfall concreto: si el LLM
del agente es un **reasoning model** con `reasoning_effort` alto (p. ej.
`openai/gpt-oss-120b` en Groq), el modelo consume el presupuesto de
`max_tokens` entero razonando internamente y devuelve `content: ""`. El parser
de consolidación explota con `ConsolidationError: "El LLM no devolvió JSON
válido. Respuesta: "` (vacía) y los recuerdos nunca se extraen.

El sub-bloque `memory.llm` permite **override parcial** de `llm.*` SOLO para
consolidación, sin tocar el LLM conversacional:

```yaml
providers:
  groq:   { api_key: KEY_GROQ, base_url: https://api.groq.com/openai/v1 }
  openai: { api_key: KEY_OPENAI }

llm:                          # Base (chat del agente)
  provider: groq
  model: openai/gpt-oss-120b
  reasoning_effort: high
  max_tokens: 2048

memory:
  enabled: true
  llm:                        # Override SOLO para consolidación
    provider: openai          # distinto vendor — creds se resuelven desde providers.openai
    model: gpt-4o-mini
    reasoning_effort: null    # apaga el reasoning
    max_tokens: 8192
    # temperature → heredado de llm.*
```

**Semántica del merge (field-by-field):**

| YAML de `memory.llm.*` | Comportamiento |
|------------------------|----------------|
| Clave AUSENTE | Hereda el valor de `llm.*`. |
| Clave con valor concreto (ej. `max_tokens: 8192`) | Pisa al base. |
| Clave con valor `null` explícito (ej. `reasoning_effort: null`) | Pisa al base con `None` (override, no herencia). |

**Validación al arrancar:** si el override apunta a un `provider` que no existe
en el registro `providers:` y el adapter correspondiente requiere credenciales,
el daemon falla al arranque con `ConfigError` — no silenciosamente durante la
siguiente consolidación.

**Wiring:** `AgentContainer` compara la `LLMConfig` mergeada contra `llm.*`;
si son idénticas, **reusa** la misma instancia de provider (sin duplicación de
HTTP clients). Si difieren, instancia un provider dedicado vía
`LLMProviderFactory.create_from_resolved(resolved)`, donde el `ResolvedLLMConfig`
compone el override con las credenciales del registry.

**Cuándo usarlo:**

- Tu LLM de chat es un reasoning model y la consolidación se rompe → caso
  canónico. Apuntá a un modelo no-reasoning (`llama-3.3-70b-versatile`,
  `gpt-4o-mini`, etc.).
- Querés un modelo más **barato** para consolidación — es extracción
  estructurada, no necesita el modelo más potente.
- El chat tira de un provider y la memoria de otro (p. ej. chat en Ollama
  local, consolidación en Groq para rapidez nocturna).

**Cuándo NO usarlo:** si tu LLM base ya funciona bien para consolidación,
omití el bloque entero. El comportamiento por defecto (`memory.llm` ausente)
reusa el provider del agente.

## Scheduler — `channel_fallback` (routing de canales)

El scheduler puede agendar tareas desde cualquier canal inbound (CLI, REST,
daemon, Telegram). Al dispararse, el `ChannelRouter` resuelve el `target` del
mensaje contra una cascada de fallbacks. Nunca falla por "canal no soportado":
si nada matchea, el mensaje se escribe en un archivo hardcoded.

### Cascada de resolución

Dado un `target` de forma `<prefix>:<destino>` (p. ej. `cli:local`, `telegram:12345`):

1. **Sink nativo** — si el `prefix` tiene un sink registrado en el container
   (hoy: `telegram`), usa ese sink directamente.
2. **Override** — si `channel_fallback.overrides[<prefix>]` existe, se
   redirige al target ahí configurado.
3. **Default** — si `channel_fallback.default` está seteado, se redirige ahí.
4. **Hardcoded** — último recurso: `file:///tmp/inaki-schedule-output.log`.
   Siempre funciona (crea el archivo y directorio si no existe).

### Sinks soportados

| Prefix | Descripción | Ejemplo target |
|--------|-------------|----------------|
| `telegram:` | Envía vía el bot de Telegram registrado. | `telegram:12345` |
| `file://` | Append a archivo. Crea dir padre. Sin sandbox. | `file:///var/log/inaki.log` |
| `null:` | Descarta silenciosamente. | `null:` |

### Ejemplos de config

```yaml
# Ejemplo 1: mandar todo lo que venga de CLI/REST/daemon a Telegram.
scheduler:
  channel_fallback:
    overrides:
      cli: "telegram:12345"
      rest: "telegram:12345"
      daemon: "telegram:12345"
```

```yaml
# Ejemplo 2: default uniforme — lo que no sea nativo va a un archivo.
scheduler:
  channel_fallback:
    default: "file:///home/pi/.inaki/data/schedule-output.log"
```

```yaml
# Ejemplo 3: silenciar un canal específico, resto al default.
scheduler:
  channel_fallback:
    default: "telegram:99999"
    overrides:
      daemon: "null:"    # daemon no notifica a nadie
```

### Trazabilidad

Cada envío persiste en `task_logs.metadata` (JSON) un par
`{original_target, resolved_target}`. Ejemplo de query:

```sql
SELECT task_id, metadata FROM task_logs WHERE status = 'success';
-- → {"original_target":"cli:local","resolved_target":"file:///tmp/inaki-schedule-output.log"}
```

Útil para auditar dónde cayó realmente un mensaje cuando hubo un fallback.

### FileSink — formato de línea

```
2026-04-15T03:00:00+00:00 | texto del mensaje
```

Una línea por envío, timestamp ISO8601 UTC. Append-only.
