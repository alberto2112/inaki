# Tools and Skills — Structure and Conventions

This document covers the two agent extension mechanisms: **tools** (functions invocable by the LLM) and **skills** (instructions injected into the system prompt). They are distinct concepts with distinct lifecycles.

---

## Comparison Table

| Aspect | Tools | Skills |
|--------|-------|--------|
| Location | `adapters/outbound/tools/` (Python) | `skills/` (YAML) |
| Base interface | `ITool` + `IToolExecutor` | `ISkillRepository` |
| Registration | Manual in `AgentContainer._register_tools()` | Automatic via glob `*.yaml` |
| Invocable by the LLM | Yes (function calling) | No (text only in the prompt) |
| Semantic routing | Yes (cosine similarity) | Yes (cosine similarity) |
| Configuration | Hardcoded in the class | `config/global.yaml` |

---

## Tools

### Interface

```python
# core/ports/outbound/tool_port.py

class ITool(ABC):
    name: str              # snake_case, e.g.: "shell_exec"
    description: str       # What it does — read by the LLM
    parameters_schema: dict  # JSON Schema (OpenAI function calling format)
    routing_keywords: str = ""  # Multilingual triggers — ONLY for routing embedding, never sent to the LLM

    async def execute(self, **kwargs) -> ToolResult: ...


class ToolResult(BaseModel):
    tool_name: str
    output: str
    success: bool
    error: str | None = None
```

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| File | `<name>_tool.py` | `shell_tool.py` |
| Class | `<Name>Tool` | `ShellTool` |
| `ITool.name` | snake_case | `"shell_exec"` |

### Minimal Example

```python
# adapters/outbound/tools/echo_tool.py
import asyncio
from core.ports.outbound.tool_port import ITool, ToolResult


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

Tools are registered manually in the container. After creating the class, add it in:

```python
# infrastructure/container.py

def _register_tools(self) -> None:
    from adapters.outbound.tools.echo_tool import EchoTool
    self._tools.register(EchoTool())
```

There is no automatic discovery. If it's not registered, it doesn't exist.

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

`YamlSkillRepository` loads via `add_file()` from the user extension `manifest.py` files (`ext/` local or `~/.inaki/ext/` in production). The core does not define built-in skills: all domain knowledge lives in extensions.

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
| Tool port | `core/ports/outbound/tool_port.py` |
| Skill port | `core/ports/outbound/skill_port.py` |
| Registry implementation | `adapters/outbound/tools/tool_registry.py` |
| Concrete tool (reference) | `adapters/outbound/tools/shell_tool.py` |
| Concrete tool (reference) | `adapters/outbound/tools/web_search_tool.py` |
| Skills implementation | `adapters/outbound/skills/yaml_skill_repo.py` |
| Manual registration | `infrastructure/container.py` |
| Usage in the pipeline | `core/use_cases/run_agent.py` |
