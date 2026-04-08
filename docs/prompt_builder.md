# Prompt Builder — Construcción del Prompt Final

## Visión general

El prompt que recibe el LLM en cada turno **no es estático**. Se construye dinámicamente en tiempo de ejecución combinando:

1. El system prompt base del agente (definido en su YAML)
2. Memorias relevantes recuperadas por RAG
3. Skills relevantes recuperadas por RAG
4. Los schemas de las tools seleccionadas (filtradas o no por RAG)

El historial de conversación se envía como lista de mensajes separada del system prompt, **truncado al máximo configurado** antes de enviarse al LLM.

---

## Flujo completo de construcción

```
RunAgentUseCase.execute(user_input)
│
├── 1. _history.load(agent_id)
│       → list[Message]  ← historial completo de data/history/active/{agent_id}.txt
│   └── trim: history[-(max_messages_in_prompt * 2):]
│       → solo los últimos N mensajes por participante (si max_messages_in_prompt > 0)
│       → el fichero en disco NO se modifica
│
├── 2. _embedder.embed_query(user_input)
│       → query_vec: list[float]
│
├── 3. _memory.search(query_vec, top_k)
│       → list[MemoryEntry]  ← memorias relevantes (cosine sim en SQLite)
│
├── 4. _skills.list_all() → all_skills
│   ├── Si len(all_skills) > cfg.skills.rag_min_skills:
│   │       _skills.retrieve(query_vec, top_k=cfg.skills.rag_top_k)
│   │       → list[Skill]  ← solo las skills relevantes
│   └── Si no:
│           retrieved_skills = all_skills  ← todas las skills
│
├── 5. AgentContext.build_system_prompt(base_prompt)
│       → system_prompt: str  ← PROMPT FINAL (detalle abajo)
│
├── 6. _tools.get_schemas() → all_schemas
│   ├── Si len(all_schemas) > cfg.tools.rag_min_tools:
│   │       _tools.get_schemas_relevant(query_vec, top_k=cfg.tools.rag_top_k)
│   │       → tool_schemas: list[dict]  ← solo las tools relevantes
│   └── Si no:
│           tool_schemas = all_schemas  ← todas las tools
│
└── 7. _llm.complete(messages, system_prompt, tools=tool_schemas)
        ↑                ↑                          ↑
    historial        prompt dinámico          schemas filtrados
    truncado
```

---

## Truncado del historial para el prompt

`history.max_messages_in_prompt` controla cuántos mensajes de cada participante se inyectan en el prompt. El fichero en disco nunca se toca.

```
max_messages_in_prompt = 21  →  history[-(21 * 2):]  →  últimos 42 mensajes
                                                          (21 del usuario + 21 del asistente)

max_messages_in_prompt = 0   →  sin truncado, historial completo
```

Configurable en `global.yaml`:

```yaml
history:
  max_messages_in_prompt: 21  # 0 = sin límite
```

---

## Construcción del system prompt (`AgentContext.build_system_prompt`)

**Archivo:** `core/domain/value_objects/agent_context.py`

El prompt se construye concatenando secciones. Solo se incluyen las secciones con contenido:

```
[base_prompt]

## Lo que recuerdas del usuario:         ← solo si hay memorias
- <memoria 1>
- <memoria 2>
- ...

## Skills disponibles:                    ← solo si hay skills
- **<nombre>**: <descripción>
  <instrucciones>
```

### Ejemplo de prompt final generado

```
Sos Iñaki, un asistente personal ágil y directo.
Respondés en español rioplatense. Usás las tools cuando hace falta.

## Lo que recuerdas del usuario:
- El usuario trabaja principalmente con Python y prefiere respuestas concisas
- El usuario tiene un servidor Raspberry Pi 5 con Ubuntu

## Skills disponibles:
- **Búsqueda Web**: Busca información en internet usando DuckDuckGo
  Cuando el usuario pregunta sobre eventos actuales, usá esta skill...
```

---

## Qué se le manda al LLM

La llamada final a `llm.complete()` recibe tres piezas:

| Parámetro | Contenido | Origen |
|-----------|-----------|--------|
| `messages` | Historial truncado + mensaje actual del usuario | `FileHistoryStore` → trim → `+ user_msg` |
| `system_prompt` | Base + memorias + skills | `AgentContext.build_system_prompt()` |
| `tools` | Schemas JSON de tools seleccionadas | `ToolRegistry.get_schemas[_relevant]()` |

---

## Selección de skills por RAG

```
len(todas las skills) > skills.rag_min_skills (default: 5)
│
├── SÍ → retrieve(query_vec, top_k=rag_top_k)
│         Cosine similarity entre query_vec y embeddings pre-indexados de cada skill
│         → Solo las top_k skills más relevantes para el mensaje actual
│
└── NO → list_all() → todas las skills sin filtrar
```

```yaml
skills:
  rag_min_skills: 5
  rag_top_k: 3
```

---

## Selección de tools por RAG

```
len(todas las tools) > tools.rag_min_tools (default: 10)
│
├── SÍ → get_schemas_relevant(query_vec, top_k=rag_top_k)
│         Cosine similarity entre query_vec y embedding de cada tool.description
│         → Solo las top_k tools más relevantes para el mensaje actual
│
└── NO → get_schemas() → todas las tools sin filtrar
```

```yaml
tools:
  rag_min_tools: 10
  rag_top_k: 5
  tool_call_max_iterations: 5  # máximo de reintentos en el loop de tool calls
```

---

## Ciclo de vida de los embeddings

| Embedding | Cuándo se calcula | Quién lo calcula | Para qué |
|-----------|-------------------|------------------|----------|
| `embed_query(user_input)` | Cada turno | `RunAgentUseCase` | Buscar memorias, skills y tools relevantes |
| `embed_passage(skill description)` | Al arrancar (lazy) | `YamlSkillRepository` | Índice de skills |
| `embed_passage(tool.description)` | Antes del primer RAG de tools (lazy) | `ToolRegistry` | Índice de tools |
| `embed_passage(fact.content)` | Durante consolidación | `ConsolidateMemoryUseCase` | Guardar memoria a largo plazo |

Los embeddings de skills y tools se calculan **una sola vez** al primer uso y se cachean en memoria. No se persisten a disco.

---

## Historial: qué se guarda y qué no

Solo los mensajes `user` y `assistant` se persisten en `data/history/active/{agent_id}.txt`.

Los mensajes de tool calls y tool results son **efímeros** — existen solo en `working_messages` durante el loop de ejecución y nunca se escriben a disco. Esto mantiene el historial limpio y legible.

```
Persiste en disco:          Solo en memoria durante el turno:
─────────────────           ──────────────────────────────────
user: ...                   tool_call: { name: "shell", args: ... }
assistant: ...              tool_result: "[shell]: output..."
user: ...
assistant: ...
```

---

## Cómo inspeccionar el prompt en tiempo real

```bash
# One-shot desde terminal
python main.py --inspect "busca el precio del dolar"
python main.py --agent dev --inspect "ejecuta los tests"

# Interactivo dentro del chat CLI
/inspect busca el precio del dolar
```

El comando `inspect` corre el pipeline completo (embedding → RAG → truncado → construcción del prompt → selección de tools) **sin llamar al LLM ni persistir nada**, e imprime:

- Memorias recuperadas
- Skills seleccionadas (con indicación de si el RAG de skills está activo)
- Tools enviadas al LLM (con indicación de si el RAG de tools está activo)
- System prompt final completo
