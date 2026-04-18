# Iñaki — Especificación Técnica del Proyecto

> Documento de referencia para el desarrollo del agente Iñaki.  
> Usar como contexto inicial completo para Claude Code.

---

## 1. Visión General

Iñaki es un asistente personal agentico impulsado por IA, diseñado para ejecutarse como servicio systemd en una **Raspberry Pi 5 (4GB RAM)**. El proyecto sigue una **arquitectura hexagonal (Ports & Adapters)** estricta para garantizar modularidad, testabilidad y escalabilidad.

### Principios de diseño

- **El core no conoce el mundo exterior.** Ningún archivo de `core/` puede importar de `adapters/` ni de librerías de infraestructura.
- **Dirección de dependencias inviolable:** `adapters/` → `core/`. Nunca al revés.
- **Un único punto de wiring:** `infrastructure/container.py` es el único lugar donde se instancian y conectan adaptadores concretos.
- **Configuración centralizada:** toda configuración proviene de archivos YAML en `config/`. Sin hardcoded values en ningún otro lugar.
- **Diseñado para la Pi 5:** RAM footprint, compatibilidad ARM64 y coste de tokens son restricciones de primera clase.

---

## 2. Stack Tecnológico

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.11+ |
| Hardware destino | Raspberry Pi 5, 4GB RAM, ARM64 |
| Despliegue | systemd service |
| LLM routing | groq API |
| Embeddings | `multilingual-e5-small` (ONNX) |
| Vector store | `sqlite-vec` + SQLite3 |
| Config | YAML + `pydantic-settings` |
| Validación de datos | `pydantic` v2 |
| Tests | `pytest` + `pytest-asyncio` |
| Inbound CLI | argparse / typer |
| Inbound Telegram | `python-telegram-bot` |
| Inbound REST API | `FastAPI` + `uvicorn` (para app Android) |
| HTTP client | `httpx` (async) |

---

## 3. Estructura de Directorios

```
inaki/
│
├── core/                                  # Hexágono: lógica pura, sin dependencias externas
│   ├── domain/
│   │   ├── entities/
│   │   │   ├── message.py                 # Message, Role enum
│   │   │   ├── memory.py                  # MemoryEntry
│   │   │   ├── skill.py                   # Skill, SkillResult
│   │   │   └── task.py                    # ScheduledTask, TaskStatus, TaskType
│   │   └── value_objects/
│   │       ├── embedding.py               # Embedding(vector, model)
│   │       └── agent_context.py           # AgentContext (estado por turno)
│   │
│   ├── ports/
│   │   ├── inbound/
│   │   │   ├── agent_port.py              # IAgentUseCase
│   │   │   └── scheduler_port.py          # ISchedulerUseCase
│   │   └── outbound/
│   │       ├── llm_port.py                # ILLMProvider
│   │       ├── memory_port.py             # IMemoryRepository
│   │       ├── embedding_port.py          # IEmbeddingProvider
│   │       ├── tool_port.py               # IToolExecutor, ITool
│   │       ├── skill_port.py              # ISkillRepository
│   │       └── history_port.py            # IHistoryStore
│   │
│   └── use_cases/
│       ├── run_agent.py                   # RunAgentUseCase
│       ├── consolidate_memory.py          # ConsolidateMemoryUseCase
│       └── schedule_task.py              # ScheduleTaskUseCase
│
├── adapters/
│   ├── inbound/
│   │   ├── cli/
│   │   │   └── cli_runner.py
│   │   ├── telegram/
│   │   │   ├── bot.py
│   │   │   └── message_mapper.py
│   │   └── rest/                          # Para app Android (FastAPI)
│   │       ├── app.py
│   │       ├── routers/
│   │       │   └── agents.py
│   │       └── schemas.py
│   │
│   └── outbound/
│       ├── providers/                         # Adaptadores LLM — descubrimiento dinámico
│       │   ├── base.py                        # BaseLLMProvider (ABC + contrato común)
│       │   ├── openrouter.py                  # PROVIDER_NAME = "openrouter"
│       │   ├── ollama.py                      # PROVIDER_NAME = "ollama"
│       │   ├── openai.py                      # PROVIDER_NAME = "openai"
│       │   └── groq.py                        # PROVIDER_NAME = "groq"
│       ├── embedding/                         # Adaptadores embedding — descubrimiento dinámico
│       │   ├── base.py                        # BaseEmbeddingProvider (ABC + contrato común)
│       │   └── e5_onnx.py                     # PROVIDER_NAME = "e5_onnx"
│       ├── memory/
│       │   └── sqlite_memory_repo.py
│       ├── tools/
│       │   ├── tool_registry.py
│       │   ├── shell_tool.py
│       │   └── web_search_tool.py
│       ├── skills/
│       │   └── yaml_skill_repo.py
│       └── history/
│           └── file_history_store.py      # Gestión de historial en fichero de texto
│
├── infrastructure/
│   ├── container.py                       # DI / wiring único
│   ├── config.py                          # Settings con pydantic-settings
│   ├── logging_setup.py
│   └── factories/
│       ├── llm_factory.py                 # Descubrimiento dinámico de providers/
│       └── embedding_factory.py           # Descubrimiento dinámico de embedding/
│
├── config/
│   ├── global.yaml                        # Config base (provider, modelo, embedding, memoria...)
│   ├── global.secrets.yaml                # Secrets globales (api keys, etc.) — gitignoreado
│   └── agents/
│       ├── general.yaml                   # Config agente + overrides + channels (sin secrets)
│       ├── general.secrets.yaml           # Secrets del agente general — gitignoreado
│       ├── dev.yaml
│       ├── dev.secrets.yaml               # Secrets del agente dev — gitignoreado
│       └── ...                            # Un par yaml/secrets por agente
│
├── skills/                                # Definiciones YAML de skills
│   └── example_skill.yaml
│
├── data/
│   ├── inaki.db                           # SQLite principal
│   └── history/
│       ├── active/                        # Historial activo por agente
│       └── archive/                       # Historiales consolidados
│
├── models/                                # Modelos ONNX locales
│   └── e5-small/
│
├── tests/
│   ├── unit/
│   │   ├── use_cases/
│   │   └── domain/
│   └── integration/
│
├── main.py
├── pyproject.toml
└── .gitignore                             # Incluye *.secrets.yaml
```

---

## 4. Entidades del Dominio

### `Message`
```python
from enum import Enum
from pydantic import BaseModel

class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class Message(BaseModel):
    role: Role
    content: str
```

### `MemoryEntry`
```python
from datetime import datetime
from pydantic import BaseModel

class MemoryEntry(BaseModel):
    id: str                          # UUID
    content: str                     # Texto del recuerdo
    embedding: list[float]           # Vector generado por E5
    relevance: float                 # Estimada por el LLM extractor (0.0 - 1.0)
    tags: list[str]                  # Etiquetas semánticas
    created_at: datetime
    agent_id: str | None = None      # None = recuerdo global compartido
```

### `AgentContext`
```python
from pydantic import BaseModel
from core.domain.entities.memory import MemoryEntry
from core.domain.entities.skill import Skill

class AgentContext(BaseModel):
    agent_id: str
    memories: list[MemoryEntry]
    skills: list[Skill]

    def build_system_prompt(self, base_prompt: str) -> str:
        """Construye el system prompt dinámico inyectando memoria y skills relevantes."""
        sections = [base_prompt]
        if self.memories:
            mem_block = "\n".join(f"- {m.content}" for m in self.memories)
            sections.append(f"\n## Lo que recuerdas del usuario:\n{mem_block}")
        if self.skills:
            skill_block = "\n".join(f"- {s.name}: {s.description}" for s in self.skills)
            sections.append(f"\n## Skills disponibles:\n{skill_block}")
        return "\n".join(sections)
```

---

## 5. Puertos (Contratos)

### `ILLMProvider`
```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from core.domain.entities.message import Message

class ILLMProvider(ABC):

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> str: ...

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]: ...
```

### `IMemoryRepository`
```python
from abc import ABC, abstractmethod
from core.domain.entities.memory import MemoryEntry

class IMemoryRepository(ABC):

    @abstractmethod
    async def store(self, entry: MemoryEntry) -> None: ...

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[MemoryEntry]: ...

    @abstractmethod
    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]: ...
```

### `IEmbeddingProvider`
```python
from abc import ABC, abstractmethod

class IEmbeddingProvider(ABC):

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Prefijo 'query:' aplicado internamente."""
        ...

    @abstractmethod
    async def embed_passage(self, text: str) -> list[float]:
        """Prefijo 'passage:' aplicado internamente."""
        ...
```

### `IHistoryStore`
```python
from abc import ABC, abstractmethod
from core.domain.entities.message import Message

class IHistoryStore(ABC):

    @abstractmethod
    async def append(self, agent_id: str, message: Message) -> None: ...

    @abstractmethod
    async def load(self, agent_id: str) -> list[Message]: ...

    @abstractmethod
    async def archive(self, agent_id: str) -> str:
        """Mueve el historial activo a /archive. Devuelve la ruta del archivo."""
        ...

    @abstractmethod
    async def clear(self, agent_id: str) -> None:
        """Elimina el historial activo (usar tras archivar)."""
        ...
```

### `IToolExecutor` / `ITool`
```python
from abc import ABC, abstractmethod
from pydantic import BaseModel

class ToolResult(BaseModel):
    tool_name: str
    output: str
    success: bool
    error: str | None = None

class ITool(ABC):
    name: str
    description: str
    parameters_schema: dict  # JSON Schema

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

class IToolExecutor(ABC):

    @abstractmethod
    def register(self, tool: ITool) -> None: ...

    @abstractmethod
    async def execute(self, tool_name: str, **kwargs) -> ToolResult: ...

    @abstractmethod
    def get_schemas(self) -> list[dict]:
        """Devuelve los schemas de todas las tools registradas para el LLM."""
        ...
```

### `ISkillRepository`
```python
from abc import ABC, abstractmethod
from core.domain.entities.skill import Skill

class ISkillRepository(ABC):

    @abstractmethod
    async def retrieve(
        self,
        query_embedding: list[float],
        top_k: int = 3,
    ) -> list[Skill]: ...
```

---

## 6. Use Cases

### `RunAgentUseCase`

Orquesta un turno completo de conversación:

1. Cargar historial del agente
2. Generar embedding del input del usuario
3. Recuperar memorias relevantes (RAG sobre `IMemoryRepository`)
4. Recuperar skills relevantes (RAG sobre `ISkillRepository`)
5. Construir `AgentContext` y system prompt dinámico
6. Llamar al LLM con historial + tools disponibles
7. Si el LLM devuelve tool calls → ejecutar tools, añadir resultados, rellamar al LLM
8. Persistir solo los mensajes user/assistant en historial (sin tool calls)
9. Devolver respuesta final

**Nota crítica:** el historial que se guarda en fichero NO incluye mensajes de tipo `tool` ni `tool_result`. Solo `user` y `assistant`.

### `ConsolidateMemoryUseCase`

Ejecutado cuando el usuario emite el comando `consolidate`:

1. Cargar historial completo del agente desde `IHistoryStore`
2. Enviar historial al LLM con prompt extractor de memoria
3. El LLM devuelve JSON con lista de recuerdos:
```json
[
  {
    "content": "Al prefiere respuestas concisas y directas",
    "relevance": 0.9,
    "tags": ["preferencias", "comunicación"]
  }
]
```
4. Para cada recuerdo: generar embedding con `IEmbeddingProvider.embed_passage()`
5. Construir `MemoryEntry` con id UUID, timestamp, embedding, relevance y tags
6. Persistir en `IMemoryRepository`
7. Si todo OK: archivar historial con `IHistoryStore.archive()` y luego `clear()`
8. Si el LLM falla: NO archivar, mantener historial intacto para reintento
9. Devolver resumen: N recuerdos extraídos, ruta del archivo

**El archivado es transaccional:** solo ocurre si la extracción y persistencia son exitosas.

---

## 7. Configuración YAML

### Sistema de configuración: cuatro capas de merge

La configuración final de cada agente se construye mergeando cuatro ficheros en orden. Cada capa sobreescribe solo los campos que define — nunca elimina campos heredados ausentes.

```
global.yaml                →  config base del sistema
    ↓ merge campo a campo
global.secrets.yaml        →  secrets globales (api keys compartidas)
    ↓ merge campo a campo
agents/{id}.yaml           →  config y canales del agente (sin secrets)
    ↓ merge campo a campo
agents/{id}.secrets.yaml   →  secrets del agente (opcional)
    ↓
AgentConfig resuelto y completo
```

**Regla crítica de merge de secrets:** si un agente no define un secret (ej: `llm.api_key`), se hereda del global sin modificación. Un secret ausente en el nivel inferior nunca nullifica el del nivel superior. Esto permite que múltiples agentes compartan la misma api key del LLM sin repetirla.

**Arranque con secrets ausentes:** si `agents/{id}.secrets.yaml` no existe, el agente arranca con un `WARNING` en el log. Los canales que requieren secrets desactivados no se levantan. El CLI siempre funciona.

**Ficheros gitignoreados:** `*.secrets.yaml` debe estar en `.gitignore`. Los ficheros `.yaml` sin secrets son commiteables.

---

### `config/global.yaml`
```yaml
app:
  name: "Iñaki"
  log_level: "INFO"
  default_agent: "general"       # Agente usado por CLI si no se pasa --agent

llm:
  provider: "openrouter"
  base_url: "https://openrouter.ai/api/v1"
  model: "anthropic/claude-3-5-haiku"
  temperature: 0.7
  max_tokens: 2048
  # api_key: en global.secrets.yaml

embedding:
  model_dirname: "models/e5-small"
  dimension: 384

memory:
  db_filename: "data/inaki.db"
  default_top_k: 5

history:
  active_dir: "~/.inaki/data/history/active"
  archive_dir: "~/.inaki/data/history/archive"
```

### `config/global.secrets.yaml` *(gitignoreado)*
```yaml
llm:
  api_key: "sk-or-..."
```

---

### `config/agents/general.yaml`
```yaml
id: "general"
name: "Iñaki-g"
description: "Asistente personal de uso general"
system_prompt: |
  Eres Iñaki, un asistente personal inteligente.
  Eres conciso, directo y útil.

# Override LLM — solo los campos que cambian, el resto se hereda
llm:
  model: "anthropic/claude-3-5-haiku"

# Canales: estructura y config no sensible
# Los valores sensibles (tokens, keys) van en general.secrets.yaml
channels:
  telegram:
    allowed_user_ids: ["123456789"]
    reactions: true
    debug: false
  rest:
    host: "0.0.0.0"
    port: 6498
```

### `config/agents/general.secrets.yaml` *(gitignoreado)*
```yaml
channels:
  telegram:
    token: "7xxxxxxx:AAF..."
  rest:
    auth_key: "sxc-0123456"
```

---

### `config/agents/dev.yaml`
```yaml
id: "dev"
name: "Iñaki-dev"
description: "Especialista en desarrollo de software"
system_prompt: |
  Eres Iñaki en modo desarrollador.
  Experto en Python, Rust y arquitecturas de software.
  Respondes con código cuando es apropiado.

# Override LLM — api_key se hereda del global.secrets.yaml
llm:
  model: "anthropic/claude-sonnet-4-5"
  max_tokens: 4096

channels:
  rest:
    host: "0.0.0.0"
    port: 6499
```

### `config/agents/dev.secrets.yaml` *(gitignoreado)*
```yaml
channels:
  rest:
    auth_key: "sxc-7891011"
# llm.api_key no definido → hereda de global.secrets.yaml
```

---

### Reglas de override

| Campo | Comportamiento |
|---|---|
| `llm` (bloque) | Merge campo a campo. Campos no definidos se heredan. |
| `llm.api_key` | Solo en secrets. Si ausente en agente → hereda del global. |
| `embedding` | Merge campo a campo si se define. |
| `memory` | Merge campo a campo si se define. |
| `channels` | Solo en el agente. No existe en global. |
| `channels.*.token` / `auth_key` | Solo en `*.secrets.yaml`. Nunca en el yaml commitable. |
| `system_prompt` | Requerido en cada agente. Sin valor por defecto. |
| `id`, `name`, `description` | Requeridos en cada agente. |

**Advertencia de responsabilidad del administrador:** el sistema no valida conflictos entre agentes (mismo puerto REST, mismo token Telegram). Es responsabilidad del administrador asegurarse de que los canales no se interfieren entre sí.

---

## 8. Sistema Multi-Agente

### Carga de configuración

Al arrancar, el sistema:

1. Carga `global.yaml` como configuración base
2. Escanea todos los ficheros en `config/agents/`
3. Para cada agente, hace merge campo a campo con el global (el agente tiene prioridad)
4. Construye un `AgentConfig` resuelto y completamente poblado por agente
5. Levanta simultáneamente todos los agentes y sus canales

```python
class AgentConfig(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str
    llm: LLMConfig          # ya resuelto (merge global + override)
    embedding: EmbeddingConfig
    memory: MemoryConfig
    history: HistoryConfig
    channels: dict[str, dict]  # {"telegram": {...}, "rest": {...}}
```

### AgentRegistry

```python
class AgentRegistry:
    def get(self, agent_id: str) -> AgentConfig: ...
    def list_all(self) -> list[AgentConfig]: ...
    def agents_with_channel(self, channel_type: str) -> list[AgentConfig]: ...
```

### Modelo de selección de agente

- **CLI:** agente seleccionado con `--agent <id>`. Sin flag → usa `app.default_agent` del global. Los canales del agente se ignoran completamente en CLI.
- **REST API:** `agent_id` en la URL: `POST /agents/{agent_id}/chat`. Cada agente REST levanta su propio servidor en su propio puerto.
- **Telegram:** cada agente con canal telegram levanta su propio bot (su propio token). Un mensaje de Telegram llega al agente cuyo token coincide.

### CLI: comandos

```bash
python main.py                          # usa default_agent del global
python main.py chat --agent dev         # usa agente 'dev'
python main.py chat --agent list        # lista agentes disponibles con descripción
```

### Memoria: scope global

La memoria a largo plazo es **global y compartida** entre todos los agentes. El historial de conversación (corto plazo) es **privado por agente**: `data/history/active/{agent_id}.txt`.

---

## 9. Sistema de Historial y Memoria

### Formato del fichero de historial (texto plano)

```
user: hola iñaki, ¿cómo estás?
assistant: Bien, gracias. ¿En qué puedo ayudarte hoy?
user: necesito que me ayudes con una tarea de python
assistant: Claro, dime qué necesitas.
```

- Un fichero por agente: `data/history/active/{agent_id}.txt`
- Solo mensajes `user:` y `assistant:`. Nunca tool calls ni tool results.
- Al archivar: `data/history/archive/{agent_id}_{YYYYMMDD_HHMMSS}.txt`

### Flujo del comando `consolidate`

```
usuario: /consolidate
    ↓
ConsolidateMemoryUseCase.execute(agent_id)
    ↓
1. IHistoryStore.load(agent_id) → list[Message]
    ↓
2. LLM(extractor_prompt + historial) → JSON con recuerdos
    ↓  [si falla → error, historial intacto, fin]
3. Para cada recuerdo:
     embedding = IEmbeddingProvider.embed_passage(content)
     entry = MemoryEntry(content, embedding, relevance, tags, ...)
     IMemoryRepository.store(entry)
    ↓  [si falla → error, historial intacto, fin]
4. archive_path = IHistoryStore.archive(agent_id)
5. IHistoryStore.clear(agent_id)
    ↓
6. Respuesta: "✓ X recuerdos extraídos. Historial archivado en {archive_path}"
```

### Prompt extractor de memoria

```
Eres un extractor de memoria de un asistente personal.
Analiza la siguiente conversación e identifica hechos, preferencias,
información relevante y contexto importante sobre el usuario.

Devuelve ÚNICAMENTE un JSON válido con el siguiente schema, sin texto adicional:
[
  {
    "content": "descripción clara del hecho o preferencia",
    "relevance": 0.0-1.0,
    "tags": ["tag1", "tag2"]
  }
]

Conversación:
{history}
```

---

## 10. RAG Pipeline

### Embeddings con `multilingual-e5-small` (ONNX)

**Regla crítica:** este modelo requiere prefijos explícitos o la similitud coseno se degrada significativamente:
- Queries: prefijo `"query: "` → usar `embed_query()`
- Documentos/recuerdos/skills: prefijo `"passage: "` → usar `embed_passage()`

El adaptador `E5OnnxProvider` aplica estos prefijos internamente. Los use cases nunca deben añadirlos manualmente.

### Búsqueda de memoria

Se usa `sqlite-vec` para búsqueda vectorial. El pipeline en `RunAgentUseCase`:

```python
query_vec = await self._embedder.embed_query(user_input)
memories = await self._memory.search(query_vec, top_k=5)
skills = await self._skills.retrieve(query_vec, top_k=3)
```

### Schema de la tabla de memoria en SQLite

```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    relevance REAL NOT NULL,
    tags TEXT NOT NULL,          -- JSON array serializado
    created_at TEXT NOT NULL,    -- ISO 8601
    agent_id TEXT                -- NULL = global
);

-- Tabla virtual para sqlite-vec
CREATE VIRTUAL TABLE memory_embeddings USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[384]
);
```

---

## 11. REST API (App Android)

### Arquitectura: un servidor por agente

Cada agente con canal `rest` definido levanta su propia instancia FastAPI en su propio puerto. No hay un servidor REST central — cada agente es independiente.

```
Agente general  → FastAPI en puerto 6498
Agente dev      → FastAPI en puerto 6499
```

### Autenticación

Header `X-API-Key` requerido en todos los endpoints. La clave se define en el bloque `channels.rest.auth_key` del fichero del agente.

### Endpoints (por instancia de agente)

```
GET  /info                      → info del agente (id, name, description)
POST /chat                      → chat, respuesta completa JSON
POST /consolidate               → ejecutar consolidación de memoria
GET  /history                   → ver historial activo
DELETE /history                 → limpiar historial sin archivar
```

### Request/Response schemas

```python
class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    agent_id: str
    agent_name: str
    response: str

class AgentInfo(BaseModel):
    id: str
    name: str
    description: str
```

---

## 12. Provider Factories (Descubrimiento Dinámico)

Las factories escanean sus respectivas carpetas de adaptadores, importan cada módulo, leen su `PROVIDER_NAME` y construyen un registro en memoria. Añadir un nuevo proveedor = crear el fichero con `PROVIDER_NAME` correcto. Sin tocar nada más.

### Convención obligatoria para adaptadores

Todo adaptador en `adapters/outbound/providers/` y `adapters/outbound/embedding/` debe:

1. Definir `PROVIDER_NAME: str` a nivel de módulo
2. Definir exactamente una clase que herede de la base correspondiente

```python
# adapters/outbound/providers/groq.py
PROVIDER_NAME = "groq"

class GroqProvider(BaseLLMProvider):
    def __init__(self, cfg):
        self._api_key = cfg.api_key
        self._model = cfg.model
        ...
```

### `infrastructure/factories/llm_factory.py`

```python
import importlib
import pkgutil
from pathlib import Path
from core.ports.outbound.llm_port import ILLMProvider
import adapters.outbound.providers as providers_pkg

class LLMProviderFactory:

    _registry: dict[str, type] = {}

    @classmethod
    def _load(cls) -> None:
        if cls._registry:
            return  # Ya cargado
        pkg_path = Path(providers_pkg.__file__).parent
        for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
            if module_name == "base":
                continue
            module = importlib.import_module(f"adapters.outbound.providers.{module_name}")
            provider_name = getattr(module, "PROVIDER_NAME", None)
            if provider_name is None:
                continue
            from adapters.outbound.providers.base import BaseLLMProvider
            for attr in vars(module).values():
                if (isinstance(attr, type)
                        and issubclass(attr, BaseLLMProvider)
                        and attr is not BaseLLMProvider):
                    cls._registry[provider_name] = attr
                    break

    @classmethod
    def create(cls, cfg) -> ILLMProvider:
        cls._load()
        provider_name = cfg.llm.provider
        if provider_name not in cls._registry:
            available = list(cls._registry.keys())
            raise ValueError(
                f"Proveedor LLM '{provider_name}' no encontrado. "
                f"Disponibles: {available}"
            )
        return cls._registry[provider_name](cfg.llm)
```

### `infrastructure/factories/embedding_factory.py`

Misma mecánica, apuntando a `adapters/outbound/embedding/`:

```python
import importlib
import pkgutil
from pathlib import Path
from core.ports.outbound.embedding_port import IEmbeddingProvider
import adapters.outbound.embedding as embedding_pkg

class EmbeddingProviderFactory:

    _registry: dict[str, type] = {}

    @classmethod
    def _load(cls) -> None:
        if cls._registry:
            return
        pkg_path = Path(embedding_pkg.__file__).parent
        for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
            if module_name == "base":
                continue
            module = importlib.import_module(f"adapters.outbound.embedding.{module_name}")
            provider_name = getattr(module, "PROVIDER_NAME", None)
            if provider_name is None:
                continue
            from adapters.outbound.embedding.base import BaseEmbeddingProvider
            for attr in vars(module).values():
                if (isinstance(attr, type)
                        and issubclass(attr, BaseEmbeddingProvider)
                        and attr is not BaseEmbeddingProvider):
                    cls._registry[provider_name] = attr
                    break

    @classmethod
    def create(cls, cfg) -> IEmbeddingProvider:
        cls._load()
        provider_name = cfg.embedding.provider
        if provider_name not in cls._registry:
            available = list(cls._registry.keys())
            raise ValueError(
                f"Proveedor embedding '{provider_name}' no encontrado. "
                f"Disponibles: {available}"
            )
        return cls._registry[provider_name](cfg.embedding)
```

### Config correspondiente

```yaml
# global.yaml
llm:
  provider: "openrouter"
  model: "anthropic/claude-3-5-haiku"

embedding:
  provider: "e5_onnx"
  model_dirname: "models/e5-small"
  dimension: 384
```

```yaml
# agents/dev.yaml — override de proveedor completo
llm:
  provider: "ollama"
  base_url: "http://localhost:11434"
  model: "llama3.2"
```

---

## 13. Container (Dependency Injection)

```python
# infrastructure/container.py
# ÚNICO lugar donde se instancian adaptadores concretos.
# Se instancia un Container por agente.

from infrastructure.config import GlobalConfig, AgentConfig
from infrastructure.factories.llm_factory import LLMProviderFactory
from infrastructure.factories.embedding_factory import EmbeddingProviderFactory
from adapters.outbound.memory.sqlite_memory_repo import SQLiteMemoryRepository
from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository
from adapters.outbound.history.file_history_store import FileHistoryStore
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.use_cases.run_agent import RunAgentUseCase
from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase

class AgentContainer:
    """Container de dependencias para un agente concreto."""

    def __init__(self, agent_config: AgentConfig, global_config: GlobalConfig):
        cfg = agent_config  # config ya resuelta (merge global + override)

        # Factories resuelven el proveedor correcto leyendo cfg.llm.provider
        # y cfg.embedding.provider — sin imports hardcodeados
        self._embedder = EmbeddingProviderFactory.create(cfg)
        self._memory = SQLiteMemoryRepository(cfg.memory.db_filename, self._embedder)
        self._llm = LLMProviderFactory.create(cfg)
        self._skills = YamlSkillRepository(self._embedder)
        self._history = FileHistoryStore(
            active_dir=cfg.history.active_dir,
            archive_dir=cfg.history.archive_dir,
        )
        self._tools = ToolRegistry()

        self.run_agent = RunAgentUseCase(
            llm=self._llm,
            memory=self._memory,
            embedder=self._embedder,
            skills=self._skills,
            history=self._history,
            tools=self._tools,
            agent_config=agent_config,
        )

        self.consolidate_memory = ConsolidateMemoryUseCase(
            llm=self._llm,
            memory=self._memory,
            embedder=self._embedder,
            history=self._history,
            agent_id=agent_config.id,
        )


class AppContainer:
    """Container raíz. Carga todos los agentes al arrancar."""

    def __init__(self, global_config: GlobalConfig, agent_configs: list[AgentConfig]):
        self.global_config = global_config
        self.agents: dict[str, AgentContainer] = {
            cfg.id: AgentContainer(cfg, global_config)
            for cfg in agent_configs
        }

    def get_agent(self, agent_id: str) -> AgentContainer:
        if agent_id not in self.agents:
            raise AgentNotFoundError(f"Agente '{agent_id}' no encontrado")
        return self.agents[agent_id]
```

---

## 14. Gestión de Errores y Principios Transversales

### Errores del dominio

Definir excepciones propias en `core/domain/`:

```python
class IñakiError(Exception): ...
class AgentNotFoundError(IñakiError): ...
class LLMError(IñakiError): ...
class ConsolidationError(IñakiError): ...
class EmbeddingError(IñakiError): ...
```

### Reglas de seguridad para tools

El system prompt de todos los agentes debe incluir estas instrucciones explícitas:
- Nunca usar parámetros destructivos por defecto (ej: `overwrite=True`, `force=True`)
- Siempre reportar el output de las tools verbatim, sin interpretarlo ni "mejorarlo"
- Pedir confirmación explícita antes de operaciones irreversibles

### Logging

Usar el módulo estándar `logging` de Python con `structlog` para logs estructurados. Nivel configurable en `settings.yaml`. Los adaptadores loggean en su propia capa; el core usa excepciones para comunicar errores hacia arriba.

---

## 15. Testing

### Principio

Los tests unitarios del core usan **mocks de los puertos**. No requieren SQLite, ONNX, ni acceso a red. Los tests de integración prueban adaptadores concretos.

### Ejemplo de test unitario

```python
# tests/unit/use_cases/test_consolidate_memory.py
import pytest
from unittest.mock import AsyncMock, patch
from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase

@pytest.mark.asyncio
async def test_consolidation_archives_on_success():
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = '[{"content": "Le gusta Python", "relevance": 0.9, "tags": ["tech"]}]'

    mock_memory = AsyncMock()
    mock_embedder = AsyncMock()
    mock_embedder.embed_passage.return_value = [0.1] * 384
    mock_history = AsyncMock()
    mock_history.load.return_value = [...]

    use_case = ConsolidateMemoryUseCase(mock_llm, mock_memory, mock_embedder, mock_history)
    result = await use_case.execute("general")

    mock_memory.store.assert_called_once()
    mock_history.archive.assert_called_once_with("general")
    mock_history.clear.assert_called_once_with("general")

@pytest.mark.asyncio
async def test_consolidation_does_not_archive_on_llm_failure():
    mock_llm = AsyncMock()
    mock_llm.complete.side_effect = Exception("LLM timeout")
    # ... setup resto de mocks

    use_case = ConsolidateMemoryUseCase(mock_llm, mock_memory, mock_embedder, mock_history)

    with pytest.raises(ConsolidationError):
        await use_case.execute("general")

    mock_history.archive.assert_not_called()  # Crítico: no archivar si falla
```

---

## 16. Gestión de Secrets y `.gitignore`

### Ficheros a gitignorear

```gitignore
# Secrets — nunca commitear
config/*.secrets.yaml
config/agents/*.secrets.yaml
```

Todos los ficheros `.yaml` sin `.secrets` en el nombre son commiteables y no contienen valores sensibles.

### Secrets de ejemplo (para documentar qué campos existen)

Crear `config/global.secrets.yaml.example` y `config/agents/{id}.secrets.yaml.example` como referencia commitable:

```yaml
# config/global.secrets.yaml.example
llm:
  api_key: "sk-or-REPLACE_ME"
```

```yaml
# config/agents/general.secrets.yaml.example
channels:
  telegram:
    token: "REPLACE_ME"
  rest:
    auth_key: "REPLACE_ME"
```

---

## 17. Checklist de Desarrollo

Al añadir cualquier nueva funcionalidad, seguir este orden sin excepciones:

1. **Entidad/Value Object** en `core/domain/` si se introduce un nuevo concepto
2. **Puerto** en `core/ports/` si se necesita una nueva dependencia externa
3. **Use Case** en `core/use_cases/` con la orquestación
4. **Test unitario** en `tests/unit/` con mocks de los puertos
5. **Adaptador** en `adapters/outbound/` o `adapters/inbound/`
6. **Wiring** en `infrastructure/container.py`
7. **Config** en `config/settings.yaml` si requiere nuevos parámetros

**Nunca saltarse pasos. Nunca mezclar capas.**

---

## 18. Arranque del Proyecto

### Orden de implementación recomendado

Implementar en este orden para tener algo funcional lo antes posible:

1. `infrastructure/config.py` + archivos YAML base
2. Entidades del dominio (`Message`, `MemoryEntry`, `AgentContext`)
3. Puertos outbound (`ILLMProvider`, `IEmbeddingProvider`, `IHistoryStore`)
4. Adaptador `OpenRouterProvider` (LLM ya funciona)
5. Adaptador `E5OnnxProvider` (embeddings)
6. Adaptador `FileHistoryStore` (historial en fichero)
7. `RunAgentUseCase` básico (sin RAG aún)
8. Adaptador CLI (`cli_runner.py`) → **primer flujo funcional end-to-end**
9. `IMemoryRepository` + `SQLiteMemoryRepository` + `sqlite-vec`
10. RAG completo en `RunAgentUseCase`
11. `ConsolidateMemoryUseCase` + comando `/consolidate` en CLI
12. `AgentRegistry` + `agents.yaml`
13. Soporte multi-agente en CLI y `RunAgentUseCase`
14. Adaptador Telegram
15. REST API con FastAPI
16. `ISkillRepository` + `YamlSkillRepository`
17. Tool system completo

---

*Documento generado como contexto inicial para Claude Code.*
*Versión: 1.3 — Descubrimiento dinámico de providers LLM y embedding via `PROVIDER_NAME`, factories en `infrastructure/factories/`.*
