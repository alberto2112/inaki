# Tools and Skills — Structure and Conventions

This document covers the two agent extension mechanisms: **tools** (functions invocable by the LLM) and **skills** (instructions injected into the system prompt). They are distinct concepts with distinct lifecycles.

---

## Comparison Table

| Aspect | Tools | Skills |
|--------|-------|--------|
| Location | `inaki/tools/builtin/` (núcleo) o `ext/<x>/` (extensiones); las de un módulo, con su módulo (`inaki/memory/tools/`, `inaki/knowledge/tools/`, `inaki/channels/telegram/tools/`) | `ext/<x>/*.yaml` |
| Base interface | `ITool` + `IToolExecutor` | `ISkillRepository` |
| Registration | The owning module's `wiring.py` builds it; the composition root registers it | Automatic via glob `*.yaml` |
| Invocable by the LLM | Yes (function calling) | No (text only in the prompt) |
| Semantic routing | Yes (cosine similarity) | Yes (cosine similarity) |
| Configuration | Hardcoded in the class | `config/global.yaml` |

---

## Tools

### Interface

```python
# inaki/kernel/ports/tool_port.py

class ITool(ABC):
    name: str              # snake_case, e.g.: "shell_exec"
    description: str       # What it does — read by the LLM
    parameters_schema: dict  # JSON Schema (OpenAI function calling format)
    routing_keywords: str = ""  # Multilingual triggers — ONLY for routing embedding, never sent to the LLM
    requires_operator: bool = False  # True → solo se ejecuta en un turno del operador (ver abajo)
    unlocked_operations: frozenset[str] = frozenset()  # operaciones que corren SIN operador aunque la tool sea requires_operator
    operation_param: str | None = None  # parámetro que despacha la operación (casi siempre "operation")

    async def execute(self, **kwargs) -> ToolResult: ...


class ToolResult(BaseModel):
    tool_name: str
    output: str
    success: bool
    error: str | None = None
```

### Privileged tools (`requires_operator`)

Una tool que cambia el estado del sistema y solo debe correr cuando la pide el operador
(el caso real es la operación `set` de `config`) declara `requires_operator = True` en la clase:

```python
class MiToolDeOperador(ITool):
    name = "mi_tool"
    requires_operator = True
    ...
```

La tool solo DECLARA. El canal decide si el turno es de operador (`es_operador`, un bit
que pasa por `RunAgentUseCase.execute`) y `run_tool_loop` rechaza con un
`ToolResult(success=False, retryable=False)` antes de ejecutar la tool si no lo es. Por
eso:

- **No leas `ChannelContext` ni un `ContextVar` para decidir si ejecutás.** La delegación
  sync corre dentro del turno del padre y heredaría ese contexto; el kernel ya cubre el
  caso, y el scheduler, `agent_send`, la delegación y los one-shot nunca son turno de
  operador. Podés leer el contexto para otra cosa (a qué chat avisar), nunca para
  autorizarte.
- Telegram: operador = privado ∧ `allowed_user_ids` no vacía ∧ usuario listado (lista
  vacía = nadie). REST/CLI: operador por la admin key. Grupos: nunca.
- `POST /admin/tool/invoke` no pasa por el tool loop (no hay turno): su auth es la admin
  key y no aplica este chequeo.
- Si una extensión registra una tool con el nombre de una privilegiada sin declarar
  `requires_operator`, `ToolRegistry` la pisa y loguea un WARNING: el privilegio se
  pierde, y el log lo dice.
- Una tool privilegiada que además no debe verla un hijo de delegación va en
  `_TOOLS_EXCLUIDAS_DEL_HIJO` (`inaki/kernel/run_agent_one_shot.py`). No hace falta si el hijo
  puede ver la tool y alcanza con que el kernel le rechace la operación protegida (el caso de
  `config`: el hijo nunca es turno de operador, así que `set` no corre, pero `get`/`list` sí).

#### Privilegio por operación (`unlocked_operations`)

Una tool multi-operación suele tener operaciones de lectura inocuas y otras que escriben.
Protegerla entera obliga a partirla en dos tools; en cambio se declara cuáles operaciones
corren SIN operador y el resto queda protegido. El ejemplo real es `config`
(`inaki/config/tools/config_tool.py`): `get` y `list` son libres para cualquier turno,
`set` solo del operador.

```python
class ConfigTool(ITool):
    name = "config"
    requires_operator = True
    operation_param = "operation"            # el parámetro del schema que despacha
    unlocked_operations = frozenset({"get", "list"})   # `set` queda protegida
    parameters_schema = {
        "type": "object",
        "properties": {"operation": {"type": "string", "enum": ["get", "list", "set"]}, ...},
        "required": ["operation"],
    }

    async def execute(self, **kwargs) -> ToolResult:
        # Despacho y autorización salen de la MISMA función: no pueden divergir.
        operation = self.operation_of(kwargs)
        ...
```

Reglas:

- **Despachá con `self.operation_of(kwargs)`**, no con tu propio `kwargs.get("operation")`.
  `ToolRegistry.requires_operator(tool_name, kwargs)` autoriza con esa función (`strip` +
  `lower`); si despachás con otra, una variante que una ve distinto de la otra es un hueco.
- `None` (parámetro ausente o vacío) **nunca** está desbloqueado: falla cerrado.
  `unlocked_operations` vacía con `requires_operator = True` es la tool entera protegida.
- `ToolRegistry.register` valida la declaración y lanza `ValueError` (con el nombre de la tool)
  si `unlocked_operations` no es vacía y: `requires_operator` es `False`, `operation_param`
  es `None`, o alguna operación no está en el `enum` de `parameters_schema["properties"][operation_param]`.
- Los tres atributos pueden fijarse por INSTANCIA en `__init__`. `config` lo hace: sin las
  dependencias de escritura (sub-agentes, `inaki chat`) se construye solo lectura (enum
  `get`/`list`, `requires_operator = False`, sin `unlocked_operations`), y el registry la valida igual.
- El rechazo del kernel nombra la operación (`la operación 'set' de la tool 'config'`), así que el
  LLM sabe qué parte de la tool no puede pedir.
- El WARNING de pisado por nombre cubre también perder privilegio por operación: una tool que
  pisa a una privilegiada desbloqueando operaciones que la anterior protegía se loguea.

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| File | `<name>.py` en `inaki/tools/builtin/` (o `tool.py` en la extensión) | `web_search.py` |
| Class | `<Name>Tool` | `ShellTool` |
| `ITool.name` | snake_case | `"shell_exec"` |

### Minimal Example

```python
# inaki/tools/builtin/echo.py  (una extensión importa exactamente igual)
import asyncio
from inaki.tools import ITool, ToolResult


class EchoTool(ITool):
    name = "echo"
    description = "Repite el texto que recibe. Útil para depuración."
    parameters_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "El texto a repetir",
            },
        },
        "required": ["text"],
    }

    async def execute(self, text: str, **kwargs) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            output=text,
            success=True,
        )
```

Always accept `**kwargs` in `execute` to silently ignore unknown parameters without breaking.

### Registration

A builtin tool is built by the `wiring.py` of the module that owns it and registered by the composition root. For a generic tool, add it to the list in `inaki/tools/wiring.py`:

```python
# inaki/tools/wiring.py

def build_builtin_tools(cfg, *, workspace, config_store) -> list[ITool]:
    return [
        WebSearchTool(config_store=config_store),
        EchoTool(),
        ...
    ]
```

A tool that belongs to a feature (memory, knowledge, perception, a channel) goes in that module's `wiring.py` instead. There is no automatic discovery for builtins. If it's not built by a wiring, it doesn't exist. Extensions are the exception: their `manifest.py` is auto-discovered.

### Tool Semantic Routing

When the number of tools exceeds `tools.semantic_routing_min_tools` (config), the registry embeds each tool and uses cosine similarity to select the most relevant ones for the current query. Below the threshold, all tools are sent. This is NOT RAG — it is dynamic capability selection; real RAG (external knowledge retrieval) lives under `knowledge:`.

```yaml
# config/global.yaml
tools:
  semantic_routing_min_tools: 10   # Activates routing if there are more than N tools
  semantic_routing_top_k: 5        # How many tools to send to the LLM
  tool_call_max_iterations: 5
```

#### `routing_keywords` — improving cross-lingual retrieval

The text the registry embeds is **not** just `description`. It is `description` **concatenated with `routing_keywords`** (`tool_registry.py::_ensure_embeddings`). The distinction matters:

- **`description`** → sent to the LLM in the function-calling schema. Written in **English** for optimal LLM comprehension.
- **`routing_keywords`** → **never** reaches the LLM. Concatenated with `description` **only** to build the embedding used for cosine matching.

Why two fields? `multilingual-e5-small` matches a query against text far better **within the same language** than across languages. If your users speak Spanish but the `description` is in English, the cosine similarity suffers. `routing_keywords` lets you add multilingual triggers (es/en/fr) that mirror how a human actually phrases the intent — so the Spanish query "buscá en internet" matches the `web_search` tool even though its `description` is English.

```python
class WebSearchTool(ITool):
    name = "web_search"
    description = "Search the web for current information..."   # English → LLM reads this
    routing_keywords = (                                        # Multilingual → only embedded
        "busca en internet, googlea, últimas noticias, cotización, clima. "
        "search the web, look it up online, latest news, current price. "
        "cherche sur internet, dernières nouvelles, prix actuel."
    )
```

Conventions:

- **Default is `""`** → tools that don't define it embed only their `description` (100% backward-compatible). Use this for tools the LLM selects by **reasoning** (filesystem tools, `delegate`, `create_tool`).
- **Define it** for tools users invoke with **natural language** (`scheduler`, `web_search`, `memory`, `knowledge_search`).
- The embedding **cache hash includes both fields** — changing `description` OR `routing_keywords` invalidates the cache and recomputes the embedding.

---

## Skills

Skills are instructions for the LLM, not functions. They are injected into the system prompt as text. The LLM reads them to know what tools it has available or how to behave in certain contexts.

### YAML Structure

```yaml
# skills/echo.yaml
id: "echo"
name: "Echo de depuración"
description: "Repite el texto recibido para verificar que el pipeline funciona"
instructions: |
  Cuando el usuario pida verificar que las herramientas funcionan,
  usá la tool `echo` para repetir su mensaje y confirmar el resultado.
tags:
  - "depuración"
  - "testing"
```

All fields are required. `instructions` supports Markdown.

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| File | `<name>.yaml` | `echo.yaml` |
| `id` | snake_case | `"echo"` |

### Discovery

`YamlSkillRepository` loads via `add_file()` from the user extension `manifest.py` files (`app.ext_dirs`, `<home>/ext` by default; `inaki.extensions` discovers them and the composition root registers what they declare). The core does not define built-in skills: all domain knowledge lives in extensions.

### Skill Semantic Routing

Same mechanism as tools: when there are more skills than `skills.semantic_routing_min_skills`, the most similar ones to the query are selected.

```yaml
# config/global.yaml
skills:
  semantic_routing_min_skills: 5
  semantic_routing_top_k: 3
```

### How They Reach the LLM

Selected skills are injected into the system prompt as a text section:

```
## Skills disponibles:
- **Echo de depuración**: Repite el texto recibido para verificar que el pipeline funciona
  Cuando el usuario pida verificar que las herramientas funcionan, usá la tool `echo`...
```

---

## Full Flow

```
User input
    │
    ▼
embed_query() ───────────────────────────────────────────┐
    │                                                    │
    ▼                                                    ▼
Skills routing                                      Tools routing
skills.retrieve(embedding)                     tools.get_schemas_relevant(embedding)
    │                                                    │
    ▼                                                    ▼
system_prompt += skills as text              tool_schemas → LLM (function calling)
    │                                                    │
    └───────────────────────┬────────────────────────────┘
                            ▼
                     LLM.complete(messages, system_prompt, tools=tool_schemas)
                            │
                     tool_call in response?
                            │
                     YES ───┼─── NO → final response
                            │
                     tools.execute(name, **args)
                            │
                     append result → re-call LLM
                     (max tool_call_max_iterations times)
```

---

## Reference Files

| Role | File |
|------|------|
| Tool port | `inaki/kernel/ports/tool_port.py` |
| Skill port | `inaki/kernel/ports/skill_port.py` |
| Registry implementation | `inaki/tools/registry.py` (`ToolRegistry`, `instanciar_tool`) |
| Tool Config Protocol store | `inaki/tools/config_store.py` |
| Concrete tool (reference) | `inaki/tools/builtin/web_search.py` |
| Extension discovery | `inaki/extensions/loader.py` |
| Skills implementation | `inaki/skills/yaml_skill_repo.py` |
| Assembly (registers what each wiring builds) | `inaki/app/assembly.py` |
| Usage in the pipeline | `inaki/kernel/run_agent.py` |
