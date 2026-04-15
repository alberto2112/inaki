# Estructura del Proyecto — Iñaki v2

## Principios de arquitectura

Iñaki sigue una **arquitectura hexagonal (Ports & Adapters)** estricta:

- `core/` — lógica pura de dominio. **Nunca importa de `adapters/` ni de librerías de infraestructura.**
- `adapters/` — implementaciones concretas de los puertos. Pueden importar librerías externas.
- `infrastructure/` — wiring, factories, config. Único lugar donde se instancian adaptadores concretos.
- Dirección de dependencias: `adapters/` → `core/`. Nunca al revés.

---

## Árbol completo

```
inaki/
│
├── core/                                  # Hexágono: lógica pura, sin dependencias externas
│   ├── domain/
│   │   ├── entities/
│   │   │   ├── message.py                 # Message, Role (user/assistant/system/tool)
│   │   │   ├── memory.py                  # MemoryEntry (id, content, embedding, relevance, tags)
│   │   │   ├── skill.py                   # Skill, SkillResult
│   │   │   └── task.py                    # ScheduledTask, TaskStatus, TaskType
│   │   ├── value_objects/
│   │   │   ├── embedding.py               # Embedding(vector, model)
│   │   │   └── agent_context.py           # AgentContext → build_system_prompt()
│   │   └── errors.py                      # IñakiError y subclases
│   │
│   ├── ports/
│   │   ├── inbound/
│   │   │   ├── agent_port.py              # IAgentUseCase
│   │   │   └── scheduler_port.py          # ISchedulerUseCase
│   │   └── outbound/
│   │       ├── llm_port.py                # ILLMProvider (complete + stream)
│   │       ├── memory_port.py             # IMemoryRepository (store + search + get_recent)
│   │       ├── embedding_port.py          # IEmbeddingProvider (embed_query + embed_passage)
│   │       ├── tool_port.py               # ToolResult, ITool, IToolExecutor
│   │       ├── skill_port.py              # ISkillRepository (retrieve)
│   │       └── history_port.py            # IHistoryStore (append/load/archive/clear)
│   │
│   └── use_cases/
│       ├── run_agent.py                   # RunAgentUseCase — orquesta un turno de conversación
│       ├── consolidate_memory.py          # ConsolidateMemoryUseCase — extrae recuerdos del historial
│       └── schedule_task.py               # ScheduleTaskUseCase — CRUD de tareas programadas
│
├── adapters/
│   ├── inbound/                           # Canales de entrada (cómo llegan mensajes a Iñaki)
│   │   ├── cli/
│   │   │   └── cli_runner.py              # Chat interactivo por terminal
│   │   ├── telegram/
│   │   │   ├── bot.py                     # TelegramBot per-agent (PTB 21+ async)
│   │   │   └── message_mapper.py          # Update → Message, response → texto formateado
│   │   ├── rest/                          # FastAPI — para app Android
│   │   │   ├── app.py                     # create_agent_app() — una instancia por agente
│   │   │   ├── schemas.py                 # ChatRequest, ChatResponse, AgentInfo...
│   │   │   └── routers/
│   │   │       └── agents.py              # GET /info, POST /chat, POST /consolidate, GET+DELETE /history
│   │   └── daemon/
│   │       └── runner.py                  # run_daemon() — levanta todos los canales en asyncio
│   │
│   └── outbound/                          # Infraestructura externa (LLM, DB, embeddings...)
│       ├── providers/                     # Adaptadores LLM — descubrimiento dinámico por PROVIDER_NAME
│       │   ├── base.py                    # BaseLLMProvider (ABC)
│       │   ├── openrouter.py              # PROVIDER_NAME = "openrouter" ← primario
│       │   ├── ollama.py                  # PROVIDER_NAME = "ollama"
│       │   ├── openai.py                  # PROVIDER_NAME = "openai"
│       │   └── groq.py                    # PROVIDER_NAME = "groq"
│       ├── embedding/                     # Adaptadores embedding — descubrimiento dinámico
│       │   ├── base.py                    # BaseEmbeddingProvider (ABC)
│       │   └── e5_onnx.py                 # PROVIDER_NAME = "e5_onnx" — multilingual-e5-small ONNX
│       ├── memory/
│       │   └── sqlite_memory_repo.py      # SQLiteMemoryRepository — sqlite-vec KNN
│       ├── history/
│       │   └── file_history_store.py      # FileHistoryStore — historial en fichero .txt
│       ├── tools/
│       │   ├── tool_registry.py           # ToolRegistry — registro y ejecución de tools
│       │   ├── shell_tool.py              # ShellTool — ejecución de comandos shell
│       │   └── web_search_tool.py         # WebSearchTool — búsqueda DuckDuckGo
│       └── skills/
│           └── yaml_skill_repo.py         # YamlSkillRepository — cosine similarity sobre YAML
│
├── infrastructure/
│   ├── config.py                          # GlobalConfig, AgentConfig, AgentRegistry, _deep_merge()
│   ├── container.py                       # AgentContainer, AppContainer — DI wiring único
│   ├── logging_setup.py                   # structlog
│   └── factories/
│       ├── llm_factory.py                 # LLMProviderFactory — descubrimiento dinámico
│       └── embedding_factory.py           # EmbeddingProviderFactory — descubrimiento dinámico
│
├── config/                                # Configuración del sistema (4 capas de merge)
│   ├── global.yaml                        # Config base — commiteable
│   ├── global.secrets.yaml                # Secrets globales — gitignoreado
│   ├── global.secrets.yaml.example        # Referencia de secrets — commiteable
│   └── agents/
│       ├── general.yaml                   # Agente general — commiteable
│       ├── general.secrets.yaml           # Secrets del agente general — gitignoreado
│       ├── general.secrets.yaml.example   # Referencia — commiteable
│       ├── dev.yaml                       # Agente dev — commiteable
│       ├── dev.secrets.yaml               # Secrets del agente dev — gitignoreado
│       └── dev.secrets.yaml.example       # Referencia — commiteable
│
├── data/                                  # Datos en runtime (gitignoreado)
│   ├── inaki.db                           # SQLite — memorias a largo plazo
│   └── history/
│       ├── active/                        # Historiales activos: {agent_id}.txt
│       └── archive/                       # Historiales archivados: {agent_id}_{YYYYMMDD_HHMMSS}.txt
│
├── models/                                # Modelos ONNX locales (gitignoreado)
│   └── e5-small/
│       ├── model.onnx
│       └── tokenizer.json
│
├── systemd/
│   ├── inaki.service                      # Unit file para systemd (Pi 5)
│   └── install.sh                         # Script de instalación del servicio
│
├── tests/
│   ├── conftest.py                        # Fixtures compartidas (mocks de puertos)
│   ├── unit/
│   │   ├── use_cases/
│   │   │   ├── test_run_agent_basic.py    # RunAgentUseCase — flujo básico + RAG
│   │   │   └── test_consolidate_memory.py # ConsolidateMemoryUseCase — transaccionalidad
│   │   └── adapters/
│   │       └── test_file_history_store.py # FileHistoryStore — CRUD de historial
│   └── integration/
│
├── docs/                                  # Documentación del proyecto
├── main.py                                # Entry point — CLI y daemon
├── config.yaml                            # Config global documentada (referencia completa)
├── pyproject.toml                         # Dependencias y metadata
└── .gitignore
```

---

## Convención para añadir un nuevo proveedor LLM

Crear `adapters/outbound/providers/miprovider.py` con:

```python
PROVIDER_NAME = "miprovider"

class MiProvider(BaseLLMProvider):
    def __init__(self, cfg: LLMConfig) -> None: ...
    async def complete(...) -> str: ...
    async def stream(...) -> AsyncIterator[str]: ...
```

Luego en `config/global.yaml`: `llm.provider: "miprovider"`. Sin tocar nada más.

## Convención para añadir una nueva skill

Crear `skills/mi_skill.yaml`:

```yaml
id: "mi_skill"
name: "Mi Skill"
description: "Qué hace esta skill"
instructions: |
  Instrucciones detalladas para el LLM...
tags:
  - "tag1"
```

## Regla de desarrollo (spec §17)

Para cada feature nueva, en este orden estricto:
1. Entidad/Value Object en `core/domain/`
2. Puerto en `core/ports/`
3. Use Case en `core/use_cases/`
4. **Test unitario** con mocks de puertos
5. Adaptador en `adapters/`
6. Wiring en `infrastructure/container.py`
7. Config en `config/global.yaml` si requiere parámetros nuevos
