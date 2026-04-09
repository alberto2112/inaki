# Tools y Skills — Estructura y Convenios

Este documento cubre los dos mecanismos de extensión del agente: **tools** (funciones invocables por el LLM) y **skills** (instrucciones inyectadas en el system prompt). Son conceptos distintos con ciclos de vida distintos.

---

## Tabla comparativa

| Aspecto | Tools | Skills |
|---------|-------|--------|
| Ubicación | `adapters/outbound/tools/` (Python) | `skills/` (YAML) |
| Interfaz base | `ITool` + `IToolExecutor` | `ISkillRepository` |
| Registro | Manual en `AgentContainer._register_tools()` | Automático por glob `*.yaml` |
| Invocables por el LLM | Sí (function calling) | No (solo texto en el prompt) |
| RAG | Sí (cosine similarity) | Sí (cosine similarity) |
| Configuración | Hardcodeada en la clase | `config/global.yaml` |

---

## Tools

### Interfaz

```python
# core/ports/outbound/tool_port.py

class ITool(ABC):
    name: str              # snake_case, ej: "run_shell"
    description: str       # Qué hace — lo lee el LLM
    parameters_schema: dict  # JSON Schema (OpenAI function calling format)

    async def execute(self, **kwargs) -> ToolResult: ...


class ToolResult(BaseModel):
    tool_name: str
    output: str
    success: bool
    error: str | None = None
```

### Convenios de naming

| Elemento | Convención | Ejemplo |
|----------|-----------|---------|
| Archivo | `<nombre>_tool.py` | `shell_tool.py` |
| Clase | `<Nombre>Tool` | `ShellTool` |
| `ITool.name` | snake_case | `"run_shell"` |

### Ejemplo mínimo

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

Siempre aceptar `**kwargs` en `execute` para ignorar parámetros desconocidos sin romper.

### Registro

Las tools se registran manualmente en el container. Después de crear la clase, agregarla en:

```python
# infrastructure/container.py

def _register_tools(self) -> None:
    from adapters.outbound.tools.echo_tool import EchoTool
    self._tools.register(EchoTool())
```

No hay descubrimiento automático. Si no se registra, no existe.

### RAG de tools

Cuando el número de tools supera `tools.rag_min_tools` (config), el registry embede la descripción de cada tool y usa cosine similarity para seleccionar las más relevantes para la query actual. Por debajo del umbral, se envían todas.

```yaml
# config/global.yaml
tools:
  rag_min_tools: 10       # Activa RAG si hay más de N tools
  rag_top_k: 5            # Cuántas tools enviar al LLM cuando RAG está activo
  tool_call_max_iterations: 5
```

---

## Skills

Las skills son instrucciones para el LLM, no funciones. Se inyectan en el system prompt como texto. El LLM las lee para saber qué herramientas tiene disponibles o cómo comportarse en ciertos contextos.

### Estructura YAML

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

Todos los campos son obligatorios. `instructions` admite Markdown.

### Convenios de naming

| Elemento | Convención | Ejemplo |
|----------|-----------|---------|
| Archivo | `<nombre>.yaml` | `echo.yaml` |
| `id` | snake_case | `"echo"` |

### Descubrimiento

`YamlSkillRepository` hace glob sobre `skills_dir/*.yaml` de forma lazy (al primer uso). No requiere registro manual.

```yaml
# config/global.yaml
app:
  skills_dir: "skills"
```

### RAG de skills

Mismo mecanismo que tools: cuando hay más skills que `skills.rag_min_skills`, se seleccionan las más similares a la query.

```yaml
# config/global.yaml
skills:
  rag_min_skills: 5
  rag_top_k: 3
```

### Cómo llegan al LLM

Las skills seleccionadas se inyectan en el system prompt como sección de texto:

```
## Skills disponibles:
- **Echo de depuración**: Repite el texto recibido para verificar que el pipeline funciona
  Cuando el usuario pida verificar que las herramientas funcionan, usá la tool `echo`...
```

---

## Flujo completo

```
User input
    │
    ▼
embed_query() ───────────────────────────────────────────┐
    │                                                    │
    ▼                                                    ▼
Skills RAG                                          Tools RAG
skills.retrieve(embedding)                     tools.get_schemas_relevant(embedding)
    │                                                    │
    ▼                                                    ▼
system_prompt += skills como texto           tool_schemas → LLM (function calling)
    │                                                    │
    └───────────────────────┬────────────────────────────┘
                            ▼
                     LLM.complete(messages, system_prompt, tools=tool_schemas)
                            │
                     ¿tool_call en respuesta?
                            │
                     YES ───┼─── NO → respuesta final
                            │
                     tools.execute(name, **args)
                            │
                     append result → re-llamar LLM
                     (máx tool_call_max_iterations veces)
```

---

## Archivos de referencia

| Rol | Archivo |
|-----|---------|
| Puerto tool | `core/ports/outbound/tool_port.py` |
| Puerto skill | `core/ports/outbound/skill_port.py` |
| Implementación registry | `adapters/outbound/tools/tool_registry.py` |
| Tool concreta (referencia) | `adapters/outbound/tools/shell_tool.py` |
| Tool concreta (referencia) | `adapters/outbound/tools/web_search_tool.py` |
| Implementación skills | `adapters/outbound/skills/yaml_skill_repo.py` |
| Registro manual | `infrastructure/container.py` |
| Uso en el pipeline | `core/use_cases/run_agent.py` |
