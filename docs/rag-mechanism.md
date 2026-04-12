# Mecanismo RAG de Iñaki

Documentación del pipeline RAG (Retrieval-Augmented Generation) para selección dinámica de skills y tools.

---

## ¿Qué es y para qué sirve?

Cuando el agente tiene muchas skills o tools disponibles, enviárselas todas al LLM en cada turno es costoso e ineficiente. El pipeline RAG resuelve este problema: en lugar de mandar la lista completa, genera un embedding de la consulta del usuario y selecciona solo las skills/tools más similares semánticamente.

El resultado: el LLM recibe un contexto más chico y preciso, lo que mejora la calidad de respuesta y reduce tokens.

---

## Flujo general

```
Usuario escribe consulta
        │
        ▼
RunAgentUseCase.execute()
        │
        ├── list_all_skills()     → ¿Hay más de rag_min_skills? → skills_rag_active
        ├── get_all_schemas()     → ¿Hay más de rag_min_tools?  → tools_rag_active
        │
        ├── (si alguno está activo)
        │       └── embed_query(user_input)  → query_vec
        │
        ├── (si skills_rag_active)
        │       └── skills.retrieve(query_vec, top_k=rag_top_k) → retrieved_skills
        │
        ├── (si tools_rag_active)
        │       └── tools.get_schemas_relevant(query_vec, top_k=rag_top_k) → tool_schemas
        │
        ▼
AgentContext(skills=retrieved_skills)
        │
        └── build_system_prompt() → sistema con solo las skills relevantes
```

El embedding de la consulta se genera **una sola vez** aunque ambos RAGs estén activos.

---

## Flujo de selección de skills

```
add_file(path) → YamlSkillRepository._extra_files[]

list_all() / retrieve()
        │
        └── _ensure_loaded()
                │
                ├── para cada archivo YAML:
                │       │
                │       ├── read_bytes() → raw_bytes
                │       ├── md5(raw_bytes) → content_hash
                │       │
                │       ├── cache.get(content_hash, provider, dimension)
                │       │       ├── HIT  → embedding del cache (sin llamar al modelo)
                │       │       └── MISS → embedder.embed_passage(nombre + desc + tags)
                │       │                       └── cache.put(content_hash, ...)
                │       │
                │       └── append(skill, embedding)
                │
                └── _loaded = True  (no se recarga hasta add_file nuevo)

retrieve(query_vec, top_k)
        │
        ├── cosine_similarity(query_vec, emb) para cada skill
        ├── sort desc por score
        └── top_k skills → inject en system prompt
```

**Clave del hash**: el hash MD5 se calcula sobre los bytes crudos del archivo YAML completo, no sobre campos individuales. Si el archivo cambia (cualquier campo), el hash cambia y se descarta el embedding cacheado.

---

## Flujo de selección de tools

```
register(tool) → ToolRegistry._tools{}
                   └── _embeddings_ready = False (invalidar)

get_schemas_relevant(query_vec, top_k)
        │
        └── _ensure_embeddings()
                │
                ├── para cada tool no embeddeada aún:
                │       │
                │       ├── md5(tool.description.encode()) → content_hash
                │       │
                │       ├── cache.get(content_hash, provider, dimension)
                │       │       ├── HIT  → embedding del cache
                │       │       └── MISS → embedder.embed_passage(description)
                │       │                       └── cache.put(content_hash, ...)
                │       │
                │       └── _embeddings[tool.name] = embedding
                │
                └── _embeddings_ready = True

scored = cosine_similarity(query_vec, emb) para cada tool
top_k nombres → schemas de esas tools
```

**Diferencia clave con skills**: el hash de una tool se calcula sobre `tool.description` (string), no sobre bytes de archivo. Si la descripción no cambia entre reinicios, el embedding se reutiliza del cache.

---

## Similitud coseno

Implementada en `core/domain/services/similarity.py`:

```
cos_sim(a, b) = dot(a, b) / (||a|| * ||b||)
```

- Usa `numpy` internamente (float32)
- Retorna 0.0 si alguno de los vectores tiene norma cero (vector nulo)
- Escala: −1.0 (opuesto) → 0.0 (ortogonal) → 1.0 (idéntico)
- No hay umbral mínimo (threshold): siempre se toman los top-k, sin importar el score

---

## Caché de embeddings

### Puerto (interfaz)

`core/ports/outbound/embedding_cache_port.py` define `IEmbeddingCache`:

```
get(content_hash, provider, dimension) → list[float] | None
put(content_hash, provider, dimension, embedding) → None
```

### Implementación SQLite

`adapters/outbound/embedding/sqlite_embedding_cache.py` — `SqliteEmbeddingCache`:

**Schema de la tabla:**

```sql
CREATE TABLE embedding_cache (
    content_hash  TEXT    NOT NULL,
    provider      TEXT    NOT NULL,
    dimension     INTEGER NOT NULL,
    embedding     TEXT    NOT NULL,   -- JSON serializado: "[0.1, 0.2, ...]"
    created_at    TEXT    NOT NULL,
    PRIMARY KEY (content_hash, provider, dimension)
);
```

**Clave compuesta triple**: `(content_hash, provider, dimension)`. Esto permite:
- Cambiar de proveedor de embeddings (e.g. de `e5_onnx` a `openai`) sin conflictos
- Cambiar la dimensión del modelo sin conflictos
- Coexistir múltiples configuraciones en el mismo archivo `.db`

**Comportamiento hit/miss:**

```
get(hash, provider, dim)
    ├── HIT  → deserializa JSON → list[float] (sin llamar al modelo)
    └── MISS → None

put(hash, provider, dim, embedding)
    └── INSERT OR REPLACE (upsert)
```

**Robustez**: errores de SQLite en `get()` retornan `None` (comportamiento de miss). Errores en `put()` se loggean como WARNING pero no propagan excepción. El sistema funciona degradado si el cache falla.

**WAL mode**: la conexión usa `PRAGMA journal_mode=WAL` para mejor concurrencia en lecturas simultáneas.

**Nota**: el cache es **opcional**. Tanto `YamlSkillRepository` como `ToolRegistry` aceptan `cache=None`, en cuyo caso siempre llaman al embedder.

---

## Configuración

### `EmbeddingConfig` (en `infrastructure/config.py`)

| Campo | Default | Descripción |
|-------|---------|-------------|
| `provider` | `"e5_onnx"` | Proveedor de embeddings |
| `model_path` | `"models/e5-small"` | Ruta al modelo ONNX (solo e5_onnx) |
| `model` | `"text-embedding-3-small"` | Nombre de modelo (solo openai) |
| `dimension` | `384` | Dimensión del vector de embedding |
| `cache_db` | `"data/embedding_cache.db"` | Ruta al archivo SQLite del cache |

### `SkillsConfig`

| Campo | Default | Descripción |
|-------|---------|-------------|
| `rag_min_skills` | `10` | Mínimo de skills para activar RAG. Con ≤10 skills, se mandan todas |
| `rag_top_k` | `3` | Cuántas skills devuelve el retrieve |

### `ToolsConfig`

| Campo | Default | Descripción |
|-------|---------|-------------|
| `rag_min_tools` | `10` | Mínimo de tools para activar RAG |
| `rag_top_k` | `5` | Cuántas tools devuelve el retrieve |
| `tool_call_max_iterations` | `5` | Iteraciones máximas del tool loop |
| `circuit_breaker_threshold` | `2` | Fallos consecutivos antes de cortar |

---

## Inyección de skills en el system prompt

`AgentContext.build_system_prompt()` en `core/domain/value_objects/agent_context.py`:

```
system_prompt = base_prompt
    + memory_digest (si existe)
    + "## Skills disponibles:\n\n### Nombre\ndescripción\n\ninstrucciones"
    + extra_sections (e.g. agent discovery para delegación)
```

Cada skill se renderiza como un bloque markdown con heading `###`, descripción como primer párrafo, e instructions separadas por línea en blanco:

```markdown
## Skills disponibles:

### Búsqueda Web
Busca información en internet usando DuckDuckGo

Cuando el usuario pregunta sobre eventos actuales o necesita información...

### Calculadora
Realiza cálculos matemáticos

Usa esta skill cuando el usuario pide operaciones numéricas...
```

Solo las skills recuperadas por RAG (o todas si RAG inactivo) aparecen en el prompt.

---

## Arquitectura hexagonal

| Capa | Archivo | Rol |
|------|---------|-----|
| **Core — Puerto** | `core/ports/outbound/embedding_cache_port.py` | Interfaz `IEmbeddingCache` |
| **Core — Puerto** | `core/ports/outbound/embedding_port.py` | Interfaz `IEmbeddingProvider` |
| **Core — Puerto** | `core/ports/outbound/skill_port.py` | Interfaz `ISkillRepository` |
| **Core — Servicio** | `core/domain/services/similarity.py` | Función `cosine_similarity` |
| **Core — Value Object** | `core/domain/value_objects/agent_context.py` | Construcción del system prompt |
| **Core — Use Case** | `core/use_cases/run_agent.py` | Orquestación del pipeline RAG |
| **Adapter** | `adapters/outbound/embedding/sqlite_embedding_cache.py` | Implementación SQLite del cache |
| **Adapter** | `adapters/outbound/skills/yaml_skill_repo.py` | Carga de skills + RAG |
| **Adapter** | `adapters/outbound/tools/tool_registry.py` | Registro de tools + RAG |
| **Infraestructura** | `infrastructure/container.py` | Wiring: instancia y conecta todo |
| **Config** | `infrastructure/config.py` | `EmbeddingConfig`, `SkillsConfig`, `ToolsConfig` |

La regla hexagonal se respeta: el core no conoce SQLite ni YAML. Solo depende de las interfaces (`IEmbeddingCache`, `IEmbeddingProvider`, `ISkillRepository`).
