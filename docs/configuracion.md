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
  skills_dir: "skills"    # Directorio de YAML de skills
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
  db_path: "data/inaki.db"  # Base de datos SQLite con sqlite-vec
  default_top_k: 5          # Número de recuerdos recuperados por RAG

history:
  active_dir: "data/history/active"   # Historiales activos: {agent_id}.txt
  archive_dir: "data/history/archive" # Historiales archivados: {agent_id}_{ts}.txt
```

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

# Overrides de memoria (opcional)
# memory:
#   default_top_k: 10

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
| `memory` | Merge campo a campo si se define. |
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
  skills_dir: "/home/pi/inaki/skills"

embedding:
  model_path: "/home/pi/inaki/models/e5-small"

memory:
  db_path: "/home/pi/inaki/data/inaki.db"

history:
  active_dir: "/home/pi/inaki/data/history/active"
  archive_dir: "/home/pi/inaki/data/history/archive"
```

---

## Añadir un nuevo agente

1. Crear `config/agents/miagente.yaml` con `id`, `name`, `description`, `system_prompt`
2. Crear `config/agents/miagente.secrets.yaml` con los tokens necesarios
3. Reiniciar el daemon: `systemctl restart inaki`

El `AgentRegistry` escanea automáticamente `config/agents/*.yaml` al arrancar.
No hay registro manual ni reinicio del código.
