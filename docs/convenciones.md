# Convenciones técnicas por subsistema

Detalle de los invariantes de código agrupados por subsistema. `CLAUDE.md` lista
la versión de una línea; acá está el porqué y el detalle de implementación.

> **Convención de referencias**: los nombres en `kebab-case` que aparecen entre
> paréntesis (ej. "ver `in-flight-message-injection`") son **notas de migración** —
> están en [`migraciones.md`](migraciones.md), donde se explica el bug que las originó.
> Las reglas estructurales (canal THIN, tiers de recursos, wiring) están en
> [`arquitectura.md`](arquitectura.md).

Índice:

- [Turno del agente (RunAgent)](#turno-del-agente-runagent)
- [Tools](#tools)
- [Routing semántico de tools](#routing-semantico-de-tools)
- [Knowledge](#knowledge)
- [Scheduler](#scheduler)
- [Canales](#canales)
- [Config y setup](#config-y-setup)
- [Fotos y reconocimiento facial](#fotos-y-reconocimiento-facial)
- [Datos y embeddings](#datos-y-embeddings)

## Turno del agente (RunAgent)

- **RunAgent — fases del turno** — `RunAgentUseCase._execute_turn` es un orquestador delgado: las fases (semantic routing + sticky, knowledge pre-fetch, presupuesto de tokens, ensamblado de mensajes, secciones in-flight, debug de foto) viven como funciones libres en `core/use_cases/_turn_pipeline.py` — mismo contrato que `_tool_loop.py`: dependencias explícitas (ports, settings VO, VOs), sin `self`, testeables aisladas. `run_semantic_routing` devuelve un `RoutingOutcome` (incluye `query_vec` para reusar en `prefetch_knowledge`, que también comparte `inspect()`). Para tocar una fase: editar la función en `_turn_pipeline.py`, NO re-inline en el use case.
- **Tool loop** — LLM can call tools iteratively up to `tools.tool_call_max_iterations` (default 5) with a circuit breaker for repeated failures. Entre iteraciones drena mensajes in-flight del usuario (ver `in-flight-message-injection`) y respeta el kill-switch `/stop` (ver `turn-kill-switch`): chequea la cancelación del scope en el checkpoint A y antes de cada tool del batch, y corta con un wrap-up. El rastro del turno se persiste en caliente o en batch según `skip_marker` (ver `incremental-persist`).

## Tools

- **Tool results** must be `ToolResult` objects, never raw strings.
- **Tool Config Protocol** — Tools que necesitan credenciales configurables por chat declaran `config_namespace` en la clase y reciben `config_store: IToolConfigStore` en el constructor (inyectado por `container.py`, también para tools de `ext/`). Persistencia en `tool_config.{namespace}` de **`config/tool_config.yaml`** — archivo PROPIO del store (dueño: el daemon), NO `global.secrets.yaml` (ese es del operador y el daemon no lo pisa). El store **lee su propio archivo al construirse** (la config sobrevive al reinicio) y `tool_config` NO participa del merge de 4 capas. Sensibles cifrados `enc:` con `~/.inaki/secret.key`. NUNCA crear un YAML de config propio por tool — eso era el patrón legacy (4 islas eliminadas); el archivo único compartido por namespace NO es una isla.
- **Workspace containment** — `read_file`, `write_file` y `patch_file` usan `workspace.containment` (strict/warn/off). `shell_exec` NO tiene contención — opera en cualquier path. Ver `docs/configuracion.md`.

## Routing semántico de tools

Ver también `docs/semantic-routing.md`.

- **Tool semantic routing** — ALL tools (including builtins) go through RAG selection when `len(all_schemas) > tools.semantic_routing_min_tools` (default 10). There is NO automatic injection of builtins as a category ("builtin" describe dónde vive el código, no cuándo debe estar visible). Only `top_k` (default 5) tools reach the LLM per turn, PLUS: (a) **`tools.pinned`** (default `["delegate"]`) — schemas siempre unionados al resultado del routing sin contar contra top_k ni consumir sticky TTL; para tools de ORQUESTACIÓN que el LLM elige por razonamiento — la query del embedding es el mensaje del USUARIO, así que el routing solo las traería si el usuario habla de ellas (caso real: el LLM quiso delegar a mitad de turno y alucinó un binario porque `delegate` no era visible); y (b) **tool page-in** — si el LLM llama una tool registrada pero no visible, `run_tool_loop` agrega su schema al set visible del turno (param `page_in_schemas`, solo lo pasa `RunAgentUseCase`; los one-shot NO, su sandbox `tools.allowed`+exclusión de `delegate` debe valer; con `tools_override` tampoco). Ver docs/semantic-routing.md → "Pinned Tools & Page-in". NUNCA pinnear builtins en bloque: ~25 schemas por turno degradan la selección del LLM y el presupuesto de tokens.
- **`ITool.routing_keywords`** — Optional field (default `""`). Content is concatenated with `description` **only for embedding** — never sent to the LLM schema. Pattern: `description` in English (LLM comprehension), `routing_keywords` in multilingual es/en/fr (retrieval). Reason: `multilingual-e5-small` matches query↔text much better within the same language than cross-lingual. Use this for tools that users invoke with natural language (scheduler, web_search, memory). Omit for tools the LLM selects by reasoning (FS tools, delegate, create_tool) — y si una de esas debe estar SIEMPRE disponible, el mecanismo es `tools.pinned`, no keywords. Cache hash includes both fields — changing either invalidates the embedding cache.

## Knowledge

Ver también `docs/knowledge.md`.

- **Knowledge — read-only vs indexable** — `IKnowledgeSource` (search) es read-only por Liskov. Las fuentes gestionables (solo `DocumentKnowledgeSource`) implementan `IIndexableKnowledgeSource` (index/ingest_file/list_files/delete_file/get_stats). La gestión (ingest/reindex/list/stats/delete) vive en `ManageKnowledgeUseCase` (recibe la **lista viva** de fuentes del orchestrator → ve las de extensiones), expuesta al LLM por la tool `knowledge_admin` y al operador por `inaki knowledge ...`. Ingest = modelo inbox: copia el archivo a la carpeta de la fuente e indexa **ignorando el glob**. Telegram NO tiene código de knowledge (ver "regla del canal THIN"). NUNCA agregar `index()` a `IKnowledgeSource`: rompería las fuentes read-only (memoria, sqlite).

## Scheduler

Ver también `docs/scheduler-spec.md`.

- **Scheduler cron evaluation** — TODA computación de "próxima ocurrencia" de un cron pasa por `core/domain/utils/cron.py::next_cron_occurrence()` (evalúa en `user.timezone`, devuelve UTC). NUNCA llamar `croniter` directo para next_run: evaluar cron en dos lugares con tz distintas causó el bug histórico de doble ejecución separada por el offset DST (repo en local, service en UTC).

## Canales

- **TelegramBot — estructura** — `bot.py` conserva wiring + auth + turno privado (`_run_pipeline`); los handlers viven en mixins por responsabilidad (`commands.py`, `media.py`, `group_flow.py`, `broadcast.py`), cada uno declarando el slice de estado que consume como anotaciones de clase (contrato mypy). El bot NO recibe `AgentContainer`/`AgentConfig`: recibe `TelegramBotPorts` + `TelegramBotSettings` (`ports.py`, tipados contra core), construidos por `build_telegram_bot_settings/ports` en `container.py`. Todo el estado se inicializa en `TelegramBot.__init__`.

## Config y setup

Ver también `docs/configuracion.md`.

- **Setup TUI — declaración de config/secretos y su BORDE** — El setup (`adapters/inbound/setup_tui/`) deriva qué editar y qué es secreto del **schema Pydantic**: un campo es secreto porque lo MARCA `Field(json_schema_extra={"secret": True})` (leído por `_schema._field_is_secret`), NO por su nombre (la heurística por nombre quedó solo como guard de tests, `test_secret_fields.py`). `iter_declared_secrets` recorre el schema efectivo y alimenta la **SecretsPage proactiva** (lista configurados + pendientes; maneja `channels` vía `channel_schemas` y dicts homogéneos como `providers`). **Borde deliberado: tools y skills quedan FUERA de este sistema** — su config es conversacional vía el Tool Config Protocol (arriba), le da libertad al dev y NO hay sección de tools/skills en el setup. NO extender el manifest declarativo a tools: no es deuda, es el límite del diseño. **Listas por valor conocido + descripciones**: los modelos de config heredan de `_ConfigBaseModel` (`use_attribute_docstrings=True`) → los docstrings de campo llegan al setup como ayuda; y los campos `str` con dominio conocido se editan como lista vía `choices.resolve_choices` (mapeo POR RUTA, ej. `llm.provider` → providers declarados; respeta los `Literal` del schema; el catálogo de adapters lo inyecta el composition root como `provider_adapters`).

## Fotos y reconocimiento facial

Ver también `docs/face-recognition.md`.

- **Photo handling** — `ProcessPhotoUseCase` orquesta reconocimiento facial (InsightFace, lazy-load en primera foto) + descripción de escena (LLM multimodal). `IVisionPort.detect_and_embed` devuelve `list[FaceDetection]` (bbox + embedding 512 floats). Ver `docs/face-recognition.md`.
- **InsightFace lazy-load** — El modelo NO se carga al arrancar el daemon. Se carga la primera vez que `IVisionPort.detect_and_embed` es llamado (singleton perezoso en `_get_app()`). Tests verifican esto mockeando el import path del adaptador.
- **faces.db** — Base de datos separada en `~/.inaki/data/faces.db`. Independiente de `history.db` e `inaki.db`. Usa sqlite-vec para embeddings FLOAT[512]. Se crea automáticamente al primer uso.
- **`schema_meta` dimension validation** — Al arrancar, el adapter de visión compara la dimensión del modelo con `schema_meta.embedding_dim` en faces.db. Si no coinciden, lanza `EmbeddingDimensionMismatchError`. Cambiar `faces.model` invalida faces.db — ver `docs/face-recognition.md`.
- **`categoria VARCHAR` pattern** — Las personas ignoradas (via `skip_face`) se persisten en `persons` con `categoria='ignorada'`. Extensible: `NULL` = persona normal, `'ignorada'` = ignorada permanentemente, futuros valores posibles sin ALTER.
- **`message_face_metadata` side-table** — En `history.db`. Key por `history.id`. `ON DELETE CASCADE` limpia metadata cuando se borra el historial.

## Datos y embeddings

- **Embedding dimension is 384** (e5-small ONNX). Changing models requires dropping and recreating the memory DB — no auto-migration.
- **All use cases** are classes with an async `execute()` method, injected via constructor in `container.py`.
- **Message roles** use `Role` enum (`Role.USER`, `Role.ASSISTANT`, etc.), not string literals.
