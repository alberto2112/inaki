# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Inaki es un asistente multi-agente con arquitectura hexagonal estricta, memoria RAG,
scheduler y delegación entre agentes. Corre en una **Raspberry Pi 5** (ARM64, 4GB) vía
systemd (`systemd/inaki.service`).

> Este documento contiene **lo esencial de cada turno**. El detalle vive en `docs/` —
> ver [Referencias](#referencias) al final. Antes de tocar una zona que tenga documento
> propio, leelo: las reglas de acá son el resumen, no la fuente de verdad completa.

## Commands

```bash
pip install -e ".[dev]"          # install with dev deps
ruff check .                     # lint
ruff format .                    # format (line-length 100)
mypy .                           # type check
pytest                           # all tests
pytest tests/unit/               # unit only
pytest tests/integration/        # integration only
pytest -k test_name              # single test
inaki config show --origin       # config efectiva con la capa de cada valor
inaki config show --secrets      # qué credenciales están puestas y cuáles faltan
inaki                            # interactive chat (default agent)
inaki chat --agent dev           # specific agent
inaki daemon                     # systemd service mode
```

No Makefile or CI. All commands are direct calls.

## Architecture

Cuatro capas. La dirección de dependencias es `adapters → core ← infrastructure`, con
`inaki/` (composition root) por encima de todo. **Nunca al revés.**

| Capa | Qué contiene | Regla dura |
|---|---|---|
| **`core/`** | Entidades, ports, use cases, servicios y errores de dominio | **NUNCA** importa `adapters/` ni `infrastructure/`. Terceros permitidos: solo `pydantic`, `croniter`, `numpy` |
| **`adapters/`** | Implementaciones de ports. Inbound (Telegram, REST admin, CLI chat) y outbound (LLM, tools, repos, embedding, skills, scheduler) | **NUNCA** importa `infrastructure/`. Si "necesita" el container o el schema → declara un Protocol/Settings VO y el composition root se lo inyecta |
| **`infrastructure/`** | Wiring y cross-cutting. `container.py` | Único lugar donde se instancian adapters y se inyectan en use cases |
| **`inaki/`** | Composition root: `cli.py`, `daemon_runner.py`, sub-CLIs | Fuera de la regla hexagonal (ensamblar es su trabajo). Los entry points NUEVOS van acá, **no** bajo `adapters/inbound/` |

`ext/` — extensiones de usuario, auto-descubiertas vía `manifest.py`.

Las reglas las verifica `tests/unit/test_architecture.py` (incluye `TYPE_CHECKING` e
imports locales). Dos de ellas son **ratchet** — el allowlist de terceros en `core/` y la
prohibición de que `adapters/` importe `infrastructure/`: sus listas `DEUDA_*` quedaron
vacías el 2026-06-13. **NUNCA agregar entradas a `DEUDA_*`**; resolvé el acoplamiento con
Settings VOs, Protocols estructurales, o reubicando el composition root a `inaki/`.

### Las tres reglas estructurales — leer antes de agregar código

Resumen operativo. El texto completo, con el porqué y los antipatrones, está en
[`docs/arquitectura.md`](docs/arquitectura.md).

1. **Canal THIN** (antes de agregar un canal) — Una **capacidad** se implementa UNA vez
   y se expone por tres superficies que comparten lógica: use case en `core/` → tool del
   LLM → gateway admin único (`POST /admin/tool/invoke`, cliente `inaki tool <name>`). Un
   **canal** (Telegram, mañana Slack) es un inbound adapter que solo traduce su I/O a un
   turno. Un canal nuevo se declara en **una** línea: su modelo en el schema + su entrada
   en `CHANNEL_SCHEMAS` (`infrastructure/config_schema.py`). De ahí lo leen el loader (que
   lo valida), el setup TUI y el generador de `config-reference.md`. **Antipatrón**: que cada canal implemente pasarelas de los CLI — es una
   explosión N×M. Excepción CERRADA: los slash commands de Telegram son el panel del
   OPERADOR (admin-only, deterministas, sin LLM); extender uno existente es aceptable,
   crear uno nuevo para una capacidad nueva NO, y **NUNCA replicarlos en un canal nuevo**.

2. **Tiers de recursos** (antes de agregar un recurso con estado) — Decidí el tier ANTES
   de escribir código, y nunca inventes un tercero:
   - **Harness-global** (`knowledge`, `scheduler`, `faces`/`photos`): singleton pesado,
     config solo en `GlobalConfig`, construido una vez en `AppContainer`. **No hay
     aislamiento per-agente — es por diseño.** ¿Hay que aislar? → otra instancia del
     arnés como proceso aparte, con `--home` / `INAKI_HOME` propio.
   - **Per-agente** (`memory`, `history`, `channels`, `llm`, `embedding`): config en
     `AgentConfig`, construido en `AgentContainer`. Aislamiento por columna `agent_id`
     (mismo fichero) o por `db_filename` distinto (aislamiento físico).

   **NUNCA** un `knowledge` o `scheduler` per-agente: rompe el tier y multiplica recursos.

3. **Wiring / DI** — `container.py` es el único lugar donde se registra un tool, provider
   o repo. Los use cases **no reciben `AgentConfig`**: reciben Settings VOs
   (`core/domain/value_objects/agent_settings.py`), mapeados en los builders públicos de
   `container.py`. Los adapters outbound usan sus propios DTOs (`Resolved*Config`) en el
   `base.py` de su familia — **NUNCA** moverlos de vuelta a `infrastructure/config.py`.
   Providers (LLM, embedding, transcripción) se auto-descubren por la constante
   `PROVIDER_NAME`; los tres registries son **independientes**.

## Configuration

Config en **`~/.inaki/`** (no en el repo). El primer arranque renderiza `global.yaml`
desde los defaults del schema (`config/global.example.yaml` es referencia autogenerada,
no se copia ni se lee). Relocalizable entera con `--home DIR` / `INAKI_HOME`.

**Merge de 2 capas** — `global.yaml` es la **base** y cada capa siguiente completa o
pisa solo los campos que declara (nunca al revés):

1. `~/.inaki/config/global.yaml`
2. `~/.inaki/agents/{id}.yaml`

La semántica completa (listas, `null`, borrado, conflictos de forma) la define
`core/domain/config_merge.py` — motor único de los cuatro carriles: carga, edición del
setup TUI, `get_effective_config` y sub-agentes efímeros.

Las credenciales viven en esas mismas capas (solo YAML, sin env vars): los ficheros se
crean con permisos **600** y están gitignoreados — **nunca commitearlos**. Un campo es
secreto por su marca en el schema (`kind == "secret"`, que enmascara en la TUI), **no**
por el fichero donde se escribe: **NUNCA** vuelvas a expresar la sensibilidad de un dato
como un split de ficheros. → `secrets-layer-eradication`

`config/tool_config.yaml` es del daemon y **no participa** del merge (ver Tool Config
Protocol en [`docs/convenciones.md`](docs/convenciones.md)).

## Testing

- `pytest-asyncio` en modo `"auto"` — sin decorador `@pytest.mark.asyncio`
- Fixtures compartidas en `tests/conftest.py`: `agent_config` (DB `:memory:`), `mock_llm`,
  `mock_memory`, `mock_embedder`, `mock_skills`, `mock_history`, `mock_tools`
- Unit tests mockean todos los adapters; los de integración usan SQLite real

## Convenciones de código

- **Idioma del código**: variables, docstrings, comentarios y mensajes de error **en
  español**. Las `description` de tools van en inglés (comprensión del LLM); los
  `routing_keywords`, multilingües es/en/fr (retrieval).
- **Use cases**: clases con un `execute()` async, inyectadas por constructor en `container.py`.
- **Tool results**: siempre objetos `ToolResult`, nunca strings crudos.
- **Message roles**: enum `Role` (`Role.USER`, `Role.ASSISTANT`, …), nunca literales string.
- **Embedding dimension = 384** (e5-small ONNX). Cambiar el modelo obliga a borrar y
  recrear la DB de memoria — no hay auto-migración.

El detalle por subsistema (turno, tools, routing, knowledge, scheduler, canales, setup,
fotos) está en [`docs/convenciones.md`](docs/convenciones.md).

## Invariantes — reglas destiladas de bugs reales

Cada una salió de un fallo en producción. El caso completo está en
[`docs/migraciones.md`](docs/migraciones.md), en la nota que se cita.

**Historial y persistencia del turno**

- **NUNCA** persistir en `history.db` desde dentro de una tool sin preguntarte quién más
  escribe ese scope en ese turno. Dos escritores rompen el grupo protocolar. → `outbound-send-single-owner`
- **NUNCA** asumir que el LLM alucina cuando niega una acción propia: primero mirá qué le
  entregó el loader. → `outbound-send-single-owner`
- **NUNCA** dejar que una tool responda "no existe" cuando lo que sabe es "no lo tengo".
  Una tool que no puede decir *no sé* fuerza al modelo a inventar certeza — y una confesión
  fantasma es tan grave como una acción fantasma. → `search-history-retention-horizon`
- **NUNCA** expresar una política de retención en FILAS cuando lo que querés acotar es
  conversación: el rastro de tools se come el presupuesto. → `trim-cuenta-conversacion`
- **NUNCA** persistir un grupo protocolar confiando en que "el load lo arregla". → `incremental-persist`
- **NUNCA** volver a un drain por conteo sobre una vista ventaneada — el cursor es por
  rowid monotónico. → `in-flight-message-injection`
- **NUNCA** cerrar el tool loop sin drenar (checkpoint C): si el ACK le prometió al usuario
  "lo incorporo", incorporalo. → `in-flight-message-injection`
- **NUNCA** persistir un borrador que el usuario no vio — no entregado ⇒ no persistido,
  misma regla que `__SKIP__`. → `in-flight-message-injection`
- **NUNCA** dejar que un drain resetee el contador de iteraciones sin tope: de esa
  contabilidad depende que el turno termine. → `in-flight-message-injection`
- **NUNCA** REEMPLAZAR el set de tools visible a mitad de turno (el re-routing in-flight
  UNE, y con techo): hay trabajo en vuelo con las viejas. → `in-flight-message-injection`
- **NUNCA** re-emitir el bloque completo de un attachment ya persistido para agregarle el
  análisis — eso es `format_analysis_delta`. → `attachment-grammar`
- **NUNCA** descartar el return del `dispatch()`: es la única vía por la que un resultado
  `bg-N` llega al usuario. → `background-delegation`

**Tools**

- **NUNCA** darle a `write_file` un modo por default sobre ficheros con contenido — el
  default ES el bug. → `write-file-explicit-mode`
- **NUNCA** hacer que un `_update` descarte campos en silencio: si un campo no se puede
  cambiar, el error tiene que decirlo. → `scheduler-trigger-type-mutable`
- **NUNCA** validar un payload contra un tipo que la misma llamada está cambiando. → `scheduler-trigger-type-mutable`
- **NUNCA** crear un YAML de config propio por tool: el store compartido por namespace ya
  existe. → `tool-config-protocol`
- **NUNCA** pinnear builtins en bloque: ~25 schemas por turno degradan la selección del
  LLM y el presupuesto de tokens. → `docs/semantic-routing.md`

**Datos y dominio**

- **NUNCA** llamar `croniter` directo para calcular `next_run`: todo pasa por
  `core/domain/utils/cron.py::next_cron_occurrence()`. Evaluar cron en dos lugares con tz
  distintas causó el bug de doble ejecución por DST.
- **NUNCA** agregar `index()` a `IKnowledgeSource`: rompería las fuentes read-only.
- **NUNCA** inventar un formato de persistencia por tipo de media o por canal — la
  gramática se extiende en `core/domain/value_objects/attachment.py`. → `attachment-grammar`
- **NUNCA** dejar un bloque de config sin tipar "para que el merge no se queje": el
  merge opera sobre dicts crudos ANTES de validar, así que tipar el destino no le
  cuesta nada. Sin tipo no se valida jamás, sus defaults se duplican en cada
  consumidor, y ninguna herramienta que lea el schema puede verlo.
  → `channels-validados-al-cargar`
- **NUNCA** escribir un segundo merge de config. La semántica vive UNA vez en
  `core/domain/config_merge.py` (dict⊕dict funde, lista reemplaza, `null` pisa,
  sentinel borra, cambiar de forma entre capas es error). Si no alcanza para un caso
  nuevo, **extendé el motor**; no nazca otro al lado. → `motor-de-merge-unico`
- **NUNCA** borrar ni renombrar un campo del schema sin migración: desde que las
  claves desconocidas abortan el arranque, quitar un campo que el bootstrap escribió
  alguna vez rompe TODAS las instalaciones existentes. → `config-limpieza-final`
- **NUNCA** documentar un parámetro de config fuera de su docstring en el schema: de
  ahí salen `config-reference.md`, `global.example.yaml` y la ayuda del setup TUI
  (`inaki gen-docs` los regenera, y un drift test los guarda). Cualquier otra copia
  nace condenada a divergir. → `docs-de-config-autogeneradas`
- **NUNCA** escribir una nota de `migraciones.md` en presente sobre el estado
  actual ("hoy todavía X", "queda pendiente Y"). Una nota es una instantánea del
  pasado: en pasado no puede caducar, en presente empieza a mentir el día que
  alguien cierra eso — y nadie relee una bitácora para corregirla. Si se cierra
  después, se agrega `*Cerrado después:*` abajo; **no se reescribe la nota**.
  → cabecera de [`migraciones.md`](docs/migraciones.md)
- **NUNCA** construir una interfaz de config sobre los ficheros crudos: se construye
  sobre la config EFECTIVA con origen (`ShowEffectiveConfigUseCase`, `inaki config
  show`). Sobre ficheros crudos + semántica de merge es el problema que el setup TUI
  lleva 5.000 líneas peleando. → `config-show-effective`
- **NUNCA** sanitizar un valor de config a un default "para no romper el arranque":
  un default silencioso que contradice el YAML es un bug que no se puede diagnosticar.
  La única degradación legítima es la de una **dependencia externa** (no de la config),
  y el log tiene que nombrar qué capacidad queda muda. → `config-falla-ruidoso`
- **NUNCA** volver al rol implícito por presencia de campo en la config de broadcast, ni
  duplicar `auth` por rol. → `broadcast-topology-config`
- **NUNCA** dejar el `bind()` de un puerto dentro de una tarea de fondo mientras el caller
  loguea éxito: un arranque que no puede fallar es un arranque que no se puede
  diagnosticar. → `broadcast-arranque-observable`
- **NUNCA** degradar a `WARNING` un wiring que se saltea recursos declarados en la config
  — el daemon arranca sano y el operador no tiene pista. → `broadcast-arranque-observable`
- **NUNCA** descartar en silencio un bloque de config escrito en el nivel equivocado: si
  nadie lo lee, avisá con el path válido. → `broadcast-arranque-observable`
- **NUNCA** aplicar el formateo de un canal en los call-sites: va en el BORDE del
  transporte, el punto por el que pasan TODAS sus salidas. Por call-site, cada camino
  nuevo nace roto. → `formato-en-el-borde-del-transporte`
- **NUNCA** formatear un caption con la lógica del texto: no se puede trocear (viaja
  pegado al media) y su límite es 1024, no 4096. El render EXPANDE — sin guarda de
  longitud rompés envíos que funcionaban. → `formato-en-el-borde-del-transporte`
- **NUNCA** reintentar un envío de media sin rebobinar el handle: ptb lo lee al
  CONSTRUIR el envío, así que el reintento sube un fichero vacío.
  → `formato-en-el-borde-del-transporte`

## Git workflow

- Never create a branch without asking me for the name first.
- Never commit without showing me the commit message for approval.
- Always ask before running `git merge` or `git push`.
- Preferred branch naming: `feature/`, `fix/`, `refactor/`, `experiment/`

## Referencias

### Arquitectura y convenciones

| Documento | Contiene |
|---|---|
| [`docs/arquitectura.md`](docs/arquitectura.md) | Texto completo de las reglas estructurales: capas, canal THIN, tiers de recursos, wiring/DI y delegación con herencia |
| [`docs/convenciones.md`](docs/convenciones.md) | Invariantes por subsistema: turno/RunAgent, tools, routing, knowledge, scheduler, canales, setup TUI, fotos |
| [`docs/migraciones.md`](docs/migraciones.md) | Historial de migraciones: breaking changes, acciones del operador, cambios de comportamiento observable |
| [`docs/modelo_de_datos.md`](docs/modelo_de_datos.md) | Entidades, value objects, jerarquía de errores, ports y `ToolResult` |
| [`docs/flujo_ejecucion.md`](docs/flujo_ejecucion.md) | El turno extremo a extremo (`RunAgentUseCase`, tool loop, fotos), arranque y bootstrap, ciclo de vida del container, consolidación y reconciliación |

### Subsistemas

| Documento | Contiene |
|---|---|
| [`docs/configuracion.md`](docs/configuracion.md) | **Índice de config** — a qué doc ir, `inaki config show`, los ficheros, las 2 capas. No lista campos |
| [`docs/config-reference.md`](docs/config-reference.md) | Referencia de config **autogenerada** desde el schema Pydantic (`GlobalConfig` / `AgentConfig`) |
| [`docs/instance-home.md`](docs/instance-home.md) | `--home` / `INAKI_HOME` y resolución de paths de runtime |
| [`docs/tool-config-protocol.md`](docs/tool-config-protocol.md) | `config/tool_config.yaml`: config de tools, cifrado en reposo, `configure` conversacional |
| [`docs/contexto-por-entidad.md`](docs/contexto-por-entidad.md) | Memoria caliente por conversación en `~/.inaki/users/` |
| [`docs/workspace-containment.md`](docs/workspace-containment.md) | `workspace.containment`: qué paths ven las file tools |
| [`docs/transcripcion.md`](docs/transcripcion.md) | Transcripción de voz en Telegram: providers, flags, flujo del handler |
| [`docs/admin-api.md`](docs/admin-api.md) | Endpoints HTTP del daemon, bodies y códigos de error |
| [`docs/prompt_builder.md`](docs/prompt_builder.md) | Construcción del prompt y tabla de variables `{{CHANNEL.*}}` |
| [`docs/semantic-routing.md`](docs/semantic-routing.md) | Selección RAG de tools, `tools.pinned` y page-in |
| [`docs/scheduler-spec.md`](docs/scheduler-spec.md) | Spec del scheduler: triggers, task kinds, dispatch |
| [`docs/knowledge.md`](docs/knowledge.md) | Fuentes de conocimiento, ingest y extensiones |
| [`docs/tools_y_skills.md`](docs/tools_y_skills.md) | Cómo escribir una tool y una skill, con ejemplos completos |
| [`docs/face-recognition.md`](docs/face-recognition.md) | Reconocimiento facial: `faces.db`, enrolado, cambio de modelo |

### Operación y pruebas manuales

| Documento | Contiene |
|---|---|
| [`docs/broadcast-smoke.md`](docs/broadcast-smoke.md) | Smoke test del broadcast TCP entre Pis + bootstrap |
| [`docs/setup-tui-smoke.md`](docs/setup-tui-smoke.md) | Smoke test manual del TUI `inaki setup` |

> Los subdirectorios de `docs/` están gitignoreados: son material de trabajo
> local (planes de refactor, borradores), no documentación del repo. Los planes
> viven en `docs/plans/` y **no se versionan** — no los enlaces desde un fichero
> tracked, que el enlace nace roto para cualquiera que clone.

**GitHub**: https://github.com/alberto2112/inaki
