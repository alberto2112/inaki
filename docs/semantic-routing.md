# Mecanismo de semantic routing de Iñaki

Documentación del pipeline de **semantic routing** para selección dinámica de skills y tools.

> **Nota**: este mecanismo NO es RAG. Selecciona capacidades (skills/tools) disponibles por similitud semántica con la query. El RAG real — recuperación de conocimiento externo (documentos, bases de datos) para inyectar en el prompt — vive en `knowledge:` y se documenta aparte.

---

## ¿Qué es y para qué sirve?

Cuando el agente tiene muchas skills o tools disponibles, enviárselas todas al LLM en cada turno es costoso e ineficiente. El semantic routing resuelve este problema: en lugar de mandar la lista completa, genera un embedding de la consulta del usuario y selecciona solo las skills/tools más similares semánticamente.

El resultado: el LLM recibe un contexto más chico y preciso, lo que mejora la calidad de respuesta y reduce tokens.

---

## Flujo general

```
Usuario escribe consulta
        │
        ▼
RunAgentUseCase.execute()
        │
        ├── list_all_skills()     → ¿Hay más de semantic_routing_min_skills? → skills_routing_active
        ├── get_all_schemas()     → ¿Hay más de semantic_routing_min_tools?  → tools_routing_active
        │
        ├── ¿input corto (< semantic_routing.min_words_threshold) Y hay sticky previo?
        │       ├── SÍ → short-input bypass:
        │       │         heredar selección del sticky previo intacta
        │       │         (no embed, no TTL decay, no persist)
        │       └── NO → seguir flujo normal:
        │                 └── embed_query(user_input) → query_vec
        │
        ├── (si skills_routing_active y NO bypass)
        │       └── skills.retrieve(query_vec, top_k, min_score) → retrieved_skills
        │
        ├── (si tools_routing_active y NO bypass)
        │       └── tools.get_schemas_relevant(query_vec, top_k, min_score) → tool_schemas
        │
        ▼
AgentContext(skills=retrieved_skills)
        │
        └── build_system_prompt() → sistema con solo las skills relevantes
```

El embedding de la consulta se genera **una sola vez** aunque ambos routings estén activos.

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

retrieve(query_vec, top_k, min_score)
        │
        ├── cosine_similarity(query_vec, emb) para cada skill
        ├── sort desc por score
        ├── filtrar score < min_score (si min_score > 0.0)
        └── top_k skills → inject en system prompt
```

**Clave del hash**: el hash MD5 se calcula sobre los bytes crudos del archivo YAML completo, no sobre campos individuales. Si el archivo cambia (cualquier campo), el hash cambia y se descarta el embedding cacheado.

---

## Flujo de selección de tools

```
register(tool) → ToolRegistry._tools{}
                   └── _embeddings_ready = False (invalidar)

get_schemas_relevant(query_vec, top_k, min_score)
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
filtrar score < min_score (si min_score > 0.0)
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
- Umbral configurable: `semantic_routing_min_score` filtra resultados por debajo del threshold antes de aplicar top_k (default 0.0 = sin filtro)

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
| `model_dirname` | `"models/e5-small"` | Directorio del modelo ONNX (relativo a `~/.inaki/`, solo e5_onnx) |
| `model` | `"text-embedding-3-small"` | Nombre de modelo (solo openai) |
| `dimension` | `384` | Dimensión del vector de embedding |
| `cache_filename` | `"data/embedding_cache.db"` | Fichero SQLite del cache (relativo a `~/.inaki/`) |

### `SkillsConfig`

| Campo | Default | Descripción |
|-------|---------|-------------|
| `semantic_routing_min_skills` | `10` | Mínimo de skills para activar routing. Con ≤10 skills, se mandan todas |
| `semantic_routing_top_k` | `3` | Cuántas skills devuelve el retrieve |
| `semantic_routing_min_score` | `0.0` | Score mínimo de cosine similarity (0.0-1.0). Skills por debajo se descartan ANTES de aplicar top_k. 0.0 = sin filtro |

### `ToolsConfig`

| Campo | Default | Descripción |
|-------|---------|-------------|
| `semantic_routing_min_tools` | `10` | Mínimo de tools para activar routing |
| `semantic_routing_top_k` | `5` | Cuántas tools devuelve el retrieve |
| `semantic_routing_min_score` | `0.0` | Score mínimo de cosine similarity (0.0-1.0). Tools por debajo se descartan ANTES de aplicar top_k. 0.0 = sin filtro |
| `tool_call_max_iterations` | `5` | Iteraciones máximas del tool loop |
| `circuit_breaker_threshold` | `2` | Fallos consecutivos antes de cortar |

### `SemanticRoutingConfig`

| Campo | Default | Descripción |
|-------|---------|-------------|
| `min_words_threshold` | `0` | Mínimo de palabras del user_input para re-correr el routing. Por debajo de este umbral (y si hay sticky previo) se saltea embedding y se hereda la selección del turno anterior intacta. `0` = feature deshabilitada (comportamiento histórico) |

---

## Gate por cantidad de palabras (short-input bypass)

Parámetro: `semantic_routing.min_words_threshold` (ver `SemanticRoutingConfig`).

**Motivación.** En follow-ups cortos — "sí", "dale", "y eso?" — el embedding tiene poca señal semántica y normalmente el contexto sigue siendo el del turno anterior. Recalcular el routing en cada turno corto (a) gasta una llamada al embedder y (b) puede "resetear" skills/tools relevantes que ya estaban seleccionadas.

**Semántica.** Al arrancar `execute()` se evalúa:

```
is_short = (
    semantic_routing.min_words_threshold > 0
    and len(user_input.split()) < semantic_routing.min_words_threshold
    and (prev_state.sticky_skills or prev_state.sticky_tools)
)
```

Si `is_short` es `True`:

- NO se llama a `embed_query` → ahorra latencia y cuota del embedder
- NO se corre `apply_sticky` → el TTL del sticky queda **congelado** (no decrementa)
- `retrieved_skills` / `tool_schemas` se reconstruyen desde `prev_state.sticky_*` (filtrando ids que ya no existan en el catálogo actual)
- `state_dirty = False` → NO se persiste estado

Si `is_short` es `False`, el pipeline original corre sin cambios.

**Casos de borde.**

- Primer turno (sticky vacío) con input corto → el routing **corre normalmente**. Sin sticky previo no hay contexto del cual heredar.
- Routing desactivado por umbrales de pool (`semantic_routing_min_skills` / `semantic_routing_min_tools` no superados) → irrelevante, ya se mandaban todas las skills/tools.
- `tools_override` activo (p. ej. scheduler `agent_send`) → el override siempre manda; el gate solo afecta skills.
- Umbral estricto: un input con exactamente `min_words_threshold` palabras **no** es corto (comparación `<`, no `<=`).

**Intención.** Política del caller del routing, no del embedder. `EmbeddingConfig` describe "cómo se calcula un embedding"; `SemanticRoutingConfig` describe "cuándo activar el pipeline de routing". Por eso no vive dentro de `EmbeddingConfig`.

**Visibilidad.** `inspect()` aplica el mismo gate que `execute()` — si el input es corto y hay sticky previo, muestra la selección heredada (no la que saldría de re-correr el routing). Así el debug refleja lo que realmente vería el LLM.

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

Solo las skills recuperadas por routing (o todas si el routing está inactivo) aparecen en el prompt.

---

## Arquitectura hexagonal

| Capa | Archivo | Rol |
|------|---------|-----|
| **Core — Puerto** | `core/ports/outbound/embedding_cache_port.py` | Interfaz `IEmbeddingCache` |
| **Core — Puerto** | `core/ports/outbound/embedding_port.py` | Interfaz `IEmbeddingProvider` |
| **Core — Puerto** | `core/ports/outbound/skill_port.py` | Interfaz `ISkillRepository` |
| **Core — Servicio** | `core/domain/services/similarity.py` | Función `cosine_similarity` |
| **Core — Value Object** | `core/domain/value_objects/agent_context.py` | Construcción del system prompt |
| **Core — Use Case** | `core/use_cases/run_agent.py` | Orquestación del pipeline de routing |
| **Adapter** | `adapters/outbound/embedding/sqlite_embedding_cache.py` | Implementación SQLite del cache |
| **Adapter** | `adapters/outbound/skills/yaml_skill_repo.py` | Carga de skills + routing |
| **Adapter** | `adapters/outbound/tools/tool_registry.py` | Registro de tools + routing |
| **Infraestructura** | `infrastructure/container.py` | Wiring: instancia y conecta todo |
| **Config** | `infrastructure/config.py` | `EmbeddingConfig`, `SkillsConfig`, `ToolsConfig`, `SemanticRoutingConfig` |

La regla hexagonal se respeta: el core no conoce SQLite ni YAML. Solo depende de las interfaces (`IEmbeddingCache`, `IEmbeddingProvider`, `ISkillRepository`).
