# Notas de migración

Historial de cambios que rompen compatibilidad, requieren acción del operador o
cambian comportamiento observable. Cada nota explica **el bug o la decisión**, el
**cambio**, y el **invariante que dejó** ("NUNCA volver a…").

Orden: **cronológico inverso** (la más reciente primero).

> Los invariantes destilados de estas notas viven en `CLAUDE.md` → "Invariantes".
> Este documento es el porqué; `CLAUDE.md` es el qué.

## Índice por acción del operador

### Requieren acción manual

| Nota | Acción |
|---|---|
| [`scheduler-trigger-type-mutable`](#scheduler-trigger-type-mutable) | **Chequeo previo al deploy**: query SQL sobre `scheduler.db` buscando filas incoherentes |
| [`tool-config-protocol`](#tool-config-protocol) | Borrar YAMLs huérfanos per-tool + `~/.inaki/.env`; reconfigurar credenciales por chat |
| [`multi-agent-telegram-broadcast`](#multi-agent-telegram-broadcast) | **Borrar** `history.db` e `inaki.db` |
| [`telegram-photo-recognition`](#telegram-photo-recognition) | **Borrar** `history.db` e `inaki.db`; agregar bloque `photos:` |
| [`broadcast-cross-agent-events`](#broadcast-cross-agent-events) | Parar el daemon en **TODOS** los Pis del LAN a la vez (wire format incompatible) |
| [`memory-management-tools`](#memory-management-tools) | `ALTER TABLE` + recrear índice parcial en `inaki.db` |
| [`memory-scoped-by-channel-chat`](#memory-scoped-by-channel-chat) | `ALTER TABLE` en caliente + revisar override de `digest_filename` |
| [`telegram-group-auth`](#telegram-group-auth) | Agregar `chat_id` a `allowed_chat_ids` o el bot deja de responder en grupos |
| [`channel-contextid`](#channel-contextid) | `mv` de los ficheros de contexto a `{context_id}.md` + cambiar la variable del prompt |
| [`per-user-context-files`](#per-user-context-files) | *(superseded)* `mv` de `USER.md` a `users/{channel}/…` |
| [`config-falla-ruidoso`](#config-falla-ruidoso) | **Puede impedir el arranque**: corregir el typo/valor que el error nombra (antes se ignoraba en silencio) |

### Migración automática en caliente — sin acción

`persist-tool-calls`, `groups-vs-broadcast`, `tool-config-own-file`,
`agent-state-scoped-by-channel-chat`, `secrets-layer-eradication`,
`channels-validados-al-cargar`, `motor-de-merge-unico`.

### Sin migración — pero cambian comportamiento observable

| Nota | Cambio observable |
|---|---|
| [`outbound-send-single-owner`](#outbound-send-single-owner) | `persist_tool_calls` pasa a `true` por default → `history.db` crece más rápido |
| [`trim-cuenta-conversacion`](#trim-cuenta-conversacion) | `keep_last_messages` cuenta conversación, no filas → se retiene **más** historial |
| [`search-history-retention-horizon`](#search-history-retention-horizon) | `search_history` declara hasta dónde llega el registro; un resultado vacío deja de leerse como "no ocurrió" |
| [`write-file-explicit-mode`](#write-file-explicit-mode) | **BREAKING de contrato**: el parámetro `overwrite` ya no existe |
| [`subagent-inheritance`](#subagent-inheritance) | El sub-agente hereda `llm`/recursos del **caller**, no de su propio YAML |
| [`in-flight-message-injection`](#in-flight-message-injection) | Dos mensajes seguidos → **una** respuesta combinada, no dos turnos |
| [`background-delegation`](#background-delegation) | `delegate` es **async por default** (`wait=false`) |
| [`drop-per-agent-rest`](#drop-per-agent-rest) | Bloques `channels.rest` se ignoran en silencio |
| [`config-show-effective`](#config-show-effective) | Comando nuevo `inaki config show`: config efectiva con origen y secretos redactados |
| [`docs-de-config-autogeneradas`](#docs-de-config-autogeneradas) | `global.example.yaml` pasa a autogenerarse; `inaki gen-docs` regenera los dos artefactos |
| [`secrets-layer-eradication`](#secrets-layer-eradication) | Desaparece la pantalla SECRETS del setup; borrar provider/agente se lleva sus credenciales |
| [`channels-validados-al-cargar`](#channels-validados-al-cargar) | Un canal desconocido o una topología de broadcast inválida se reportan con su path en vez de ignorarse |
| [`motor-de-merge-unico`](#motor-de-merge-unico) | Una clave que cambia de forma entre capas aborta con su path; antes se reemplazaba en silencio |
| [`broadcast-topology-config`](#broadcast-topology-config) | Rol explícito `server` XOR `client`; config vieja falla al cargar |
| [`broadcast-arranque-observable`](#broadcast-arranque-observable) | El fallo de `bind()` y la config de broadcast que no valida ahora salen como `ERROR` en el log |
| [`formato-en-el-borde-del-transporte`](#formato-en-el-borde-del-transporte) | Todo lo que Telegram manda fuera del turno conversacional (scheduler, `bg-N`, intermedios, media) sale **formateado** y troceado, no en markdown crudo |

## Índice por subsistema

- **Historial y persistencia del turno**: `outbound-send-single-owner`,
  `persist-tool-calls`, `incremental-persist`, `intermediate-persist`,
  `in-flight-message-injection`, `turn-kill-switch`
- **Retención y alcance del registro**: `trim-cuenta-conversacion`,
  `search-history-retention-horizon`
- **Telegram y canales**: `channels-validados-al-cargar`, `attachment-grammar`,
  `formato-en-el-borde-del-transporte`,
  `groups-vs-broadcast`,
  `broadcast-topology-config`, `broadcast-arranque-observable`,
  `broadcast-cross-agent-events`,
  `multi-agent-telegram-broadcast`, `telegram-group-auth`,
  `telegram-photo-recognition`, `drop-per-agent-rest`
- **Contexto per-entidad**: `channel-contextid`, `group-context-by-chat-id`
  *(superseded)*, `per-user-context-files` *(superseded)*
- **Memoria**: `memory-management-tools`, `memory-scoped-by-channel-chat`,
  `agent-state-scoped-by-channel-chat`
- **Scheduler**: `scheduler-trigger-type-mutable`, `channel-send-history-persist`
- **Tools y config**: `write-file-explicit-mode`, `tool-config-protocol`,
  `tool-config-own-file`, `secrets-layer-eradication`, `motor-de-merge-unico`,
  `config-falla-ruidoso`, `config-show-effective`, `docs-de-config-autogeneradas`
- **Delegación**: `subagent-inheritance`, `background-delegation`

---

### `docs-de-config-autogeneradas`

**Había cuatro fuentes describiendo los mismos parámetros, y tres estaban
desactualizadas.** `docs/configuracion.md` (1.733 líneas de prosa),
`docs/config-reference.md` (autogenerada), `config/global.example.yaml` (607
líneas escritas a mano) y los docstrings del schema. Cuatro sitios que
mantener, y ninguna garantía de que dijeran lo mismo.

Lo que estaba roto, medido:

| Artefacto | Estado |
|---|---|
| `config-reference.md` | **100 de 184 filas sin descripción** (54%) |
| `global.example.yaml` | le faltaban **5 bloques enteros** del schema (`scheduler`, `transcription`, `delegation`, `photos`, `knowledge`) |
| `configuracion.md` | una tabla de reglas de merge que el código **no imponía**, y varias afirmaciones falsas |

**El cambio.** Una sola fuente: los docstrings del schema. Se escribieron los
100 que faltaban (más 7 docstrings de clase), y `global.example.yaml` pasó a
**autogenerarse** como ya lo hacía la referencia — cabecera de cada bloque desde
el docstring de su clase, cada campo con su default y su descripción. `inaki
gen-docs` regenera los dos artefactos y el test de drift cubre ambos.

**La tabla de "field merge rules" se borró, no se implementó.** Varias filas
eran directamente falsas, y la más grave decía que `memories.db_filename` es
"solo global porque el store es compartido" — cuando `AgentContainer` lo lee de
la capa del agente, o sea que cada agente puede tener su propia DB de memoria,
que es justo lo que declara `CLAUDE.md`. La doc contradecía al código *y* a las
reglas del proyecto. En su lugar quedó la semántica única del motor de merge
([`motor-de-merge-unico`](#motor-de-merge-unico)) más una tabla de los casos que
son genuinamente especiales, cada uno verificado contra el código.

Otras dos contradicciones corregidas: `system_prompt` figuraba como *required*
cuando tiene default `""`, y `channels` como "no existe en global" cuando sí
existe con **otro significado** (la colisión de nombre con `ChannelsGlobalConfig`).

**Un guard nuevo que vale la pena.** El drift test ya no se conforma con que el
ejemplo parsee como YAML: verifica que **el propio schema lo acepte**, bloque a
bloque. Cazó `knowledge.sources` emitido como bloque anidado cuando el schema
espera una lista. Desde [`config-falla-ruidoso`](#config-falla-ruidoso) esto
importa el doble: un ejemplo que el runtime rechazaría hace que el operador
copie el bloque y el daemon no arranque.

**Invariante.** **NUNCA** documentes un parámetro fuera de su docstring en el
schema. De ahí salen la referencia, el YAML de ejemplo y la ayuda del setup TUI;
cualquier otra copia nace condenada a divergir — y una doc que miente cuesta más
que una que falta, porque se le cree.

---

### `config-show-effective`

**No había forma de preguntarle al sistema qué config estaba usando.** Para
responder "¿de dónde sale este valor?" había que abrir `global.yaml`, abrir
`agents/{id}.yaml`, recordar la semántica del merge y, para los campos que
nadie declaró, ir a buscar el default al schema. Tres fuentes y un merge mental.

**El cambio.** `inaki config show [--agent ID] [--origin] [--json] [--secrets]`
entrega la config **efectiva** —la que ve el runtime, no la que está escrita—
campo a campo, con la capa que aportó cada valor:

```
   llm.model         modelo-propio   [agent]
   llm.provider      groq            [global]
   llm.temperature   0.7             [default]
🔒 providers.groq.api_key   ********        [global]
```

Las tres capas del dump son `default` (el schema), `global` y `agent`. La de
`default` importa más de lo que parece: sin ella el comando mostraría lo
escrito, no lo que el runtime usa. Incluye los bloques que el schema marca como
requeridos (`llm`, `memories`, …) porque el loader los materializa siempre con
`Modelo(**merged.get(x, {}))` — su obligatoriedad es estructural y nunca llega
al operador.

**Los secretos salen redactados, siempre.** El output está pensado para pegarse
en un issue, así que un valor marcado como credencial en el schema se emite como
`********` y jamás en claro. Esto tapa el agujero que dejó
[`secrets-layer-eradication`](#secrets-layer-eradication): desde que las
credenciales viven en la capa principal, `cat global.yaml` dejó de ser pegable.

**Y recupera la vista transversal de credenciales.** `--secrets` responde "qué
tengo puesto y qué me falta" sin navegar el árbol — la capacidad que se perdió
al borrar la `SecretsPage`:

```
🔒 admin.auth_key            ********           [global]
🔒 channels.telegram.token   ********           [agent]
🔒 providers.groq.api_key    ********           [global]
🔒 providers.openai.api_key  (sin configurar)   [default]
```

Solo se reportan pendientes de **secciones que el operador ya declaró**: listar
el `auth` de un broadcast que nadie configuró convierte la vista en ruido de
features no usadas. Es el mismo criterio que tenía la `SecretsPage`.

**Dónde se apoya.** En dos piezas que las fases anteriores dejaron listas: la
procedencia por hoja que devuelve `merge_capas`
([`motor-de-merge-unico`](#motor-de-merge-unico)) y la marca `kind == "secret"`
del schema, que `secrets-layer-eradication` conservó al erradicar los ficheros.
El use case no conoce el schema: el composition root (`inaki/config_cli.py`) le
pasa los defaults y el set de paths secretos ya resueltos.

**Invariante.** **NUNCA** construyas una interfaz de configuración sobre los
ficheros crudos. Se construye sobre la **config efectiva con origen**: una UI
sobre eso es un problema simple; sobre N ficheros más la semántica de merge es
el problema que el setup TUI lleva 5.000 líneas peleando.

---

### `config-falla-ruidoso`

> **Nota para el operador**: esta es la única fase del refactor de config que
> puede impedir que el daemon arranque con una config que antes "funcionaba".
> Si el arranque falla tras actualizar, el mensaje nombra el fichero, el bloque
> y la clave a corregir. **Es a propósito**: lo que arrancaba, arrancaba mal.

**El subsistema de config tenía tres formas distintas de mentir.** Ninguna era
un bug puntual; las tres eran decisiones de "no molestar al operador" que
terminaban costándole mucho más caro que un error al arrancar:

| Mentira | Qué veía el operador |
|---|---|
| Claves desconocidas ignoradas (33 de 37 modelos) | `temperatura: 0.9` no hacía nada; el modelo corría con `temperature: 0.7` |
| Valores basura sanitizados a un default | `timeout_seconds: "sesenta"` corría con 60; creía tener 300 para thinking mode |
| Un agente inválido desaparecía con WARNING | El bot no responde y el daemon dice estar sano |

**El cambio.**

1. **`extra="forbid"` para TODO el schema**, puesto en la clase base en vez de
   modelo por modelo. Un validador propio se adelanta al genérico de Pydantic
   para nombrar el bloque, la clave sobrante y —vía `difflib`— la que se quiso
   escribir: `LLMConfig: clave(s) desconocida(s): 'temperatura' ¿Quisiste decir
   'temperature'?`. Los tres canales pierden su `extra="allow"`: existía
   mientras el bloque no se validaba al cargar, y desde
   [`channels-validados-al-cargar`](#channels-validados-al-cargar) ya no aplica.
2. **`timeout_seconds` y `request_delay_seconds` dejan de sanitizarse** —
   `gt=0` y `ge=0` declarativos, sin validador propio.
3. **Un agente con config inválida aborta el arranque** con `ConfigError`
   nombrando el fichero. Un agente que NO EXISTE sigue devolviendo `None`: no
   es lo mismo "no está" que "está roto".
4. **Cinco wirings del container pasan de degradar a ser fatales**: construcción
   del `AgentContainer`, delegación, scheduler, broadcast y tools de Telegram.
   Todos comparten la misma forma: el operador declaró una capacidad y el
   `except Exception → logger.error → continuar` la dejaba muda.

**Lo que se degrada, se degrada a propósito y lo dice.** El stack de visión
(InsightFace, modelos ONNX) es una dependencia externa pesada que puede faltar
en el host sin que la config esté mal, así que sigue degradando — pero el log
pasó de `Error en wire_photos para agente 'x'` a nombrar la capacidad perdida:
`el procesamiento de fotos QUEDA DESHABILITADO`. Ídem las extensiones de
`ext/`: código de terceros del usuario, que una rota tumbe el daemon sería peor
que saltearla con aviso.

**Reversión de un criterio anterior — leer esto.** `timeout_seconds` y
`request_delay_seconds` tenían fallback explícito por pedido del operador, con
la regla escrita en sus tests: *"nada de fail-fast acá: priorizamos que el
bootstrap del daemon no muera por un dedazo en el YAML"*. Esta nota **revierte
esa decisión**, y la razón es que la red resultó peor que el problema que
evitaba: un default silencioso que contradice lo escrito en el YAML no es
robustez, es un bug que no se puede diagnosticar. El daemon que no arranca se
arregla en diez segundos leyendo el error; el que arranca con un timeout que no
pediste puede pasar meses sin que lo notes.

**Invariante.** **NUNCA** sanitizar un valor de config a un default "para no
romper el arranque". Un valor mal escrito es un error del operador, y el
arranque es el único momento en que se puede señalar con precisión. La única
degradación legítima es la de una **dependencia externa** (no de la config), y
tiene que nombrar en el log qué capacidad queda muda.

---

### `motor-de-merge-unico`

**Había cinco motores de merge, y ninguno sabía de los otros.** No eran cinco
capas: eran cinco *implementaciones* de "qué significa mergear config", con
semánticas parecidas pero no idénticas, cada una nacida cuando la anterior no
alcanzaba para un caso nuevo.

| Mecanismo | Dónde vivía | Qué sabía de más / de menos |
|---|---|---|
| `_deep_merge` | `infrastructure/config_loader.py` | carril de carga; **no sabía borrar claves** |
| `_deep_merge` | `core/use_cases/config/get_effective_config.py` | copia literal del anterior, con su propio rastreo de orígenes |
| `deep_merge_con_eliminaciones` | `core/use_cases/config/_merge.py` | carril de edición; sí borra, vía sentinel |
| `resolve_inherit` | `infrastructure/config_loader.py` | herencia opt-in por bloque |
| `build_ephemeral_child` | `infrastructure/container.py` | 5ª capa en runtime, invisible a toda UI |

**El síntoma que lo delató.** El setup TUI tuvo que inventar un tri-estado
propio (`INHERIT` / `OVERRIDE_VALOR` / `OVERRIDE_NULL`) con su modal de 148
líneas para poder decir "borrá esta clave". ¿Por qué? Porque el carril de carga
**no tenía forma de expresar el borrado**: "ausente" y "borrado" se decían
distinto según el carril. El tri-estado no era complejidad de la UI; era la UI
reconstruyendo semántica que el sistema de abajo no ofrecía.

**El cambio.** Un módulo de dominio puro, `core/domain/config_merge.py`, con la
tabla de semántica documentada en un solo lugar:

| Caso | Resultado |
|---|---|
| dict ⊕ dict | merge recursivo |
| clave ausente en override | hereda de base |
| lista ⊕ lista | **reemplazo total** — nunca concatena |
| `None` explícito | pisa (es "desactivar", no "ausente") |
| `SENTINEL_ELIMINAR` | borra la clave |
| escalar ⊕ dict (o al revés) | **error ruidoso** |

Los cuatro carriles pasan a apuntar al mismo objeto: el loader y el carril de
edición vía alias que conservan sus nombres históricos, y `build_ephemeral_child`
importándolo explícitamente para que la quinta capa deje de ser un mundo aparte.
El tri-estado sobrevive, pero degradado a lo que siempre debió ser: **una
traducción** de la intención de la UI a los tres primitivos que el motor ya
entiende (sentinel / `None` / valor).

La dirección del merge queda escrita como invariante en el módulo:
`global.yaml` es la **base** y cada capa siguiente completa o pisa solo los
campos que declara. Nunca al revés, y lo mismo vale para el padre en la
herencia de sub-agentes.

**Cambios observables.**

- Una clave que cambia de forma entre capas (escalar donde antes había un
  bloque, o al revés) **aborta con su path y el nombre de la capa culpable**:
  `Conflicto de tipos en 'llm': una capa declara un bloque de config (mapa) y
  otra un valor`. Antes era un reemplazo silencioso. `None` está exento a
  propósito: apagar un bloque (`transcription: null`) es legítimo y explícito.
- `merge_capas` devuelve, además del resultado, la **procedencia de cada hoja**
  (`llm.model` → `agent`). `get_effective_config` dejó de calcularla por su
  cuenta, y es el insumo directo del `inaki config show --origin` de la Fase 5.

**Lo que NO cambió.** `merged_llm_config` (el override de `memories.llm`) queda
como **excepción única y declarada**: opera sobre modelos ya validados, no sobre
dicts crudos. Su semántica es la misma —`model_fields_set` es el equivalente
pydantic de "la clave está escrita en el YAML"— pero absorberlo obligaría a
mergear en crudo antes de validar, lo que convertiría `MemoriesConfig.llm` en un
`LLMConfig` y rompería el tri-estado que el TUI edita sobre `memories.llm.*`.
Está documentado en su docstring, con la regla: cualquier ajuste a la semántica
se hace en el motor y se replica ahí.

**Invariante.** **NUNCA** escribas un segundo merge de config. Si el que hay no
alcanza para un caso nuevo, **extendé el motor** — no nazcas otro al lado. Cinco
veces se hizo lo contrario, y el precio fue que la UI tuviera que reinventar el
borrado de claves porque el carril de carga no sabía expresarlo.

---

### `channels-validados-al-cargar`

**El 14% del schema no se validaba nunca.** `AgentConfig.channels` era un
`dict[str, dict[str, Any]]`: un dict opaco cuyos 26 campos —los de
`TelegramChannelConfig`, `TelegramGroupsConfig` y las cuatro clases de
broadcast— **no pasaban por Pydantic al cargar la config**. El tipado laxo tenía
una razón declarada en el propio código ("para sobrevivir el merge sin
validación estricta"), y esa razón se cobró tres consecuencias:

| Consecuencia | Dónde se veía |
|---|---|
| Un typo o un tipo mal puesto viajaba hasta el primer uso en runtime | cualquier campo de `channels.telegram` |
| Cada consumidor re-declaraba los defaults del schema | `bot.py` los repetía con ~30 `.get()`: tercera copia |
| El mismo campo llegaba como modelo o como dict según el camino | `hasattr(x, "model_dump")` defensivo en el bot y en el admin |
| La referencia "exhaustiva" no podía descender por el dict | 6 clases ausentes de `config-reference.md` |
| El setup TUI necesitaba un registry propio, inyectado a mano | `setup_cli.py` + un `if name == "channels"` hardcodeado |

Y una cuarta, la peor: `_wire_broadcast_for_agent` **revalidaba el bloque en un
try/except** porque no podía confiar en que estuviera validado. Cuando fallaba,
el `return` se llevaba puestos el transporte de broadcast Y el rate limiter de
grupos, con el daemon arrancando sano. Ese es literalmente el bug de
[`broadcast-arranque-observable`](#broadcast-arranque-observable), que se pudo
mitigar pero no eliminar mientras el bloque siguiera sin validarse en el borde.

**El cambio.** Un registry único, `CHANNEL_SCHEMAS`, declara qué canal existe y
con qué schema (`telegram` → `TelegramChannelConfig`, `cli` →
`CliChannelConfig`, este último tipado por primera vez). `AgentConfig.channels`
lo consume en un `field_validator(mode="before")` que valida cada bloque y lo
**coerciona a su modelo**. Vive en el schema, y no en el loader, porque así es
la ÚNICA puerta: cubre por igual los cuatro caminos que construyen un
`AgentConfig` (loader, builder efímero del flujo delegate, admin server y tests).

Con el bloque ya tipado, `AgentConfig.telegram` / `.cli` dan acceso por
atributo, y el mapeo a lo que consume el bot vive en un único builder del
composition root (`build_telegram_channel_settings` → `TelegramChannelSettings`,
VO del adapter). El bot dejó de parsear: desaparecieron sus `.get()` con
defaults y sus dos ramas `hasattr(model_dump)`.

**Cambios observables.**

- Un canal desconocido, un tipo inválido o una topología de broadcast mal
  formada **se reportan con su path exacto** (`channels.slack: canal
  desconocido. Canales soportados: cli, telegram.`) en vez de ignorarse. Un
  `broadcast:` colgado de `channels` —en vez de `channels.telegram`— ahora es un
  error de validación, no un warning sobre un bloque inerte.
- `docs/config-reference.md` pasó de 311 a 375 líneas: los canales entran como
  raíces propias del generador, porque `channels` es un dict indexado por nombre
  y la recursión por anotaciones no podía descubrirlos.

**Lo que esta nota NO cambia.** `TelegramChannelConfig` conserva
`extra="allow"`: un campo *desconocido* dentro de un canal conocido sigue
pasando sin ruido. El endurecimiento a `extra="forbid"` y el aborto del arranque
ante un agente inválido (hoy todavía desaparece con WARNING) son la Fase 4 de
[`config-refactor-plan.md`](config-refactor-plan.md).

**Invariante.** **NUNCA** dejes un bloque de config sin tipar "para que el merge
no se queje": el merge opera sobre dicts crudos ANTES de validar, así que
tipar el destino no le cuesta nada — y sin tipo, ese bloque no se valida jamás,
sus defaults se duplican en cada consumidor y ninguna herramienta que lea el
schema puede verlo.

---

### `secrets-layer-eradication`

**Los `*.secrets.yaml` prometían una separación que nadie usaba, y cobraban el
precio en todas las superficies de edición.** La config eran **4 capas**:
`global.yaml` → `global.secrets.yaml` → `agents/{id}.yaml` →
`agents/{id}.secrets.yaml`. La idea original era poder compartir o commitear la
config sin arrastrar credenciales. Nunca se usó así, y no iba a usarse.

**El diagnóstico.** La premisa que sostenía el diseño era falsa: los
`*.secrets.yaml` **nunca estuvieron cifrados**. Eran YAML plano con permisos
600 — exactamente lo mismo que su capa principal. El único cifrado real del
sistema (Fernet, valores `enc:`, clave en `~/.inaki/secret.key`) vive en
`tool_config.yaml`, que ni siquiera participa del merge. Con eso, el beneficio
neto de la separación era **compartir sin credenciales**, y el costo era:

| Costo | Dónde |
|---|---|
| 6 valores de `LayerName` en vez de 3 | `core/ports/config_repository.py` |
| Una pantalla entera de la TUI (191 líneas) | `setup_tui/screens/secrets_page.py` |
| Routing `kind == "secret"` → capa, en cada persistencia | `global_page.py`, `agent_detail_page.py` |
| Poda de claves duplicada sobre dos capas | `persist_delete` de ambas páginas |
| Dos preguntas al operador que solo existían por el split | borrar agente / borrar provider |

Y sobre todo: **una decisión no obvia por cada edición** ("¿esto va al fichero
principal o al de secrets?") en un sistema que ya tenía demasiadas.

**El cambio.** Dos capas: `config/global.yaml` → `agents/{id}.yaml`. Las
credenciales viven ahí, y **todas** las capas se crean y se reparan con permisos
600 (antes solo las de secrets). La sensibilidad de un dato dejó de ser una
propiedad del *fichero* para ser lo que siempre debió ser: una propiedad del
**schema** (`kind == "secret"`), que es lo que enmascara el valor en la TUI y lo
que permitirá redactarlo en el futuro `inaki config show`.

**Migración automática, sin acción del operador.**
`migrate_secrets_into_main_layers` corre en el bootstrap y pliega cada
`*.secrets.yaml` dentro de su capa principal (el secrets **pisa**, mismo orden
de precedencia que tenía el merge eliminado), aplica `chmod 600` y recién
entonces borra el secrets. Orden deliberado — escribir lo nuevo antes de borrar
lo viejo: en el peor caso quedan duplicados (benignos, el loader ya no los lee),
nunca pérdida de datos. Alcanza a global, agentes y sub-agentes. Es idempotente.
Corre **después** de `migrate_tool_config_to_own_file`, que necesita leer
`global.secrets.yaml` antes de que desaparezca; si ese bloque sobrevive porque
`tool_config.yaml` ya existía, se descarta en vez de ensuciar la capa principal.

**Cambios observables.** Desaparece la entrada `SECRETS` del menú de `inaki
setup` (queda GLOBAL CONFIG / AGENTS / SUBAGENTS / PROVIDERS). Borrar un
provider ahora se lleva su `api_key` en la misma operación, y borrar un agente
se lleva su único YAML con las credenciales adentro: en ambos casos se acabó la
segunda pregunta, porque ya no hay un segundo fichero que dejar huérfano.

**Los dos costos que se aceptan.** Ambos conocidos, ninguno accidental:

1. **Se pierde la vista transversal de credenciales.** La `SecretsPage` listaba
   *todos* los secretos declarados por el schema marcando cuáles estaban
   configurados y cuáles pendientes (`iter_declared_secrets`, borrada con
   ella). Esa vista no dependía del split de ficheros — era una feature propia
   que cayó con la página. Hoy hay que navegar el árbol de global/agente para
   ver una credencial.
2. **`cat global.yaml` deja de ser pegable** en un issue sin filtrar llaves a
   mano.

Los dos los cubre la misma pieza pendiente: `inaki config show --effective
--origin` con redacción de campos secretos (Fase 5 de
[`config-refactor-plan.md`](config-refactor-plan.md)) — un dump que enmascara
también responde "qué tengo configurado y qué me falta". Hasta entonces, los
huecos existen. Ojo: **esa redacción todavía NO está implementada**;
`get_effective_config` hoy devuelve los valores en claro.

**Invariante.** **NUNCA** expresar "este dato es sensible" como un split de
ficheros: es metadato del schema. Un split de ficheros por sensibilidad
multiplica capas, decisiones de escritura y superficie de UI, y no cifra nada.

---

### `formato-en-el-borde-del-transporte`

**Inaki mandaba markdown crudo por Telegram y el usuario veía los asteriscos.**
Caso real (2026-08-24): una investigación larga terminaba con `**POLKA
corresponde al ensayo NCT05898399**` — con los asteriscos a la vista, sin
negrita. Parecía un problema de prompt ("pedile al LLM que formatee distinto"),
y no lo era: **el renderer existía y funcionaba, pero no lo llamaba nadie en ese
camino**.

**El diagnóstico.** `format_response()` (markdown → subset HTML de Telegram) y
`send_html_or_plain()` (envío con `parse_mode=HTML`, troceo a 4096 y fallback a
texto plano) estaban bien hechos desde siempre, pero se invocaban en **tres
call-sites**, los tres del camino conversacional: `bot.py` (privado),
`group_flow.py` (grupo) y `media.py` (foto/audio). Todo el resto de las salidas
del canal mandaba el texto crudo:

| Camino | Qué entrega |
|---|---|
| `TelegramSink` | resultados del scheduler (`channel_send`) |
| `TelegramLiveIntermediateSink` | los bloques intermedios del tool loop |
| `background_queue_adapter._deliver_response` | los resultados `bg-N` de una delegación |
| `TelegramChannelOutbound._enviar_texto` | la superficie `IChannelOutbound` (tool `send_to_telegram`, gateway admin) |

**El error de diseño**, que es lo que importa: el formateo es responsabilidad
del canal y va en el **borde del transporte**, no en cada call-site. Aplicado por
call-site, cada camino de salida nuevo **nace roto por default** y nadie se
entera hasta que un usuario ve los asteriscos. Es la misma lógica del canal THIN:
la capacidad se implementa UNA vez, en el punto por el que pasan todos.

**El cambio.** El renderizado se mudó al borde:

- `TelegramBot.send_message` envuelve su envío en `send_html_or_plain`. Los
  cuatro caminos de la tabla resuelven el bot vía `get_telegram_bot()`, que
  devuelve la instancia de `TelegramBot` (no el `Bot` de python-telegram-bot):
  **todos convergen ahí**, así que el arreglo es uno solo.
- `TelegramBot.send_photo` / `send_audio` / `send_video` / `send_document`
  formatean su `caption` con el gemelo `send_caption_or_plain`.
- `TelegramChannelOutbound._enviar_media_group` hace lo propio para el álbum
  multi-foto — el único caption que NO pasa por `send_photo`, porque viaja
  dentro del primer `InputMediaPhoto`.

No hay riesgo de doble formateo: los tres call-sites conversacionales usan
`message.reply_text` o `self._app.bot` (el `Bot` de ptb), nunca
`TelegramBot.send_message`.

**Efecto colateral bienvenido**: esos caminos tampoco tenían troceo. Un resultado
`bg-N` de más de 4096 chars **rebotaba** con "message is too long" y el usuario
no recibía NADA. Ahora se parte solo.

**Los captions no son mensajes de texto** y por eso tienen su propia función
(`format_caption`) en vez de reusar la del texto:

- **No se pueden trocear**: viajan pegados al media, en el mismo request. No hay
  "segundo caption" al que mandar el sobrante.
- **El límite es 1024**, no 4096.

Como el render a HTML **expande** el texto (escapes `&amp;`/`&lt;`, tags
`<b></b>`), un caption que hoy entra crudo puede no entrar formateado. Sin la
guarda de longitud, formatear habría roto envíos que funcionaban: la regresión
sería peor que el bug. `format_caption` devuelve el crudo cuando el HTML no
entra — perder el formato es mejor que perder el envío.

**El reintento de un media exige rebobinar el handle.** `InputMediaPhoto` (y
`send_photo` y familia) leen el fichero **al construirse**, no al enviarse. Si el
fallback a texto plano re-envía sin un `seek(0)` previo, sube un fichero
**vacío**: el usuario recibe el caption bien formateado y la foto rota. De ahí el
`rebobinar()` del mapper y el `seek(0)` sobre todos los handles del álbum.

**Sin migración ni cambios de config.** Cambia el aspecto de lo que ya llegaba: lo
que antes salía en markdown crudo ahora sale con negritas, cursivas, citas y
código. Si algún texto dependía de leerse literal, ahora se renderiza.

**Invariantes**: NUNCA aplicar el formateo de un canal en los call-sites — va en
el borde del transporte, el punto por el que pasan TODAS sus salidas. NUNCA
formatear un caption con la lógica del texto: no se trocea y su límite es 1024,
así que el render necesita su propia guarda de longitud. NUNCA reintentar un
envío de media sin rebobinar el handle.

---

### `broadcast-arranque-observable`

**El server de broadcast no abría el puerto y el daemon no decía nada.** Caso real
(2026-08-15): Inaki configurado como server, Anacleto como client con IP, puerto y
`auth` correctos. El client no lograba conectarse nunca; en `journalctl` no había un
solo `ERROR`. El puerto simplemente no estaba escuchando.

Había **tres caminos que se tragaban el fallo**, y el síntoma es idéntico en los tres:

1. **`TcpBroadcastAdapter.start()` no bindeaba** — hacía
   `asyncio.create_task(self._ejecutar_server())` y retornaba. El
   `asyncio.start_server()` (el `bind()`) corría DENTRO de la tarea. Resultado:
   `start()` no podía fallar nunca, el `try/except` de `AppContainer.startup()` no
   tenía nada que atrapar, y el log de éxito —`"broadcast adapter iniciado"`— se
   emitía igual con el puerto cerrado. El `OSError` (`EADDRINUSE`, interfaz
   inexistente, permiso) quedaba retenido en una tarea que nadie awaitea; ni
   siquiera aparecía el `"Task exception was never retrieved"` de Python, porque el
   container conserva la referencia a `_tarea_principal` mientras vive el proceso.
2. **`_wire_broadcast_for_agent` se salteaba el wiring con un `WARNING`** — parsea
   `channels.telegram` con `TelegramChannelConfig.model_validate()`; si el bloque no
   valida, hace `return`. Ese `return` se lleva puestos DOS recursos: el transporte
   TCP **y** el rate limiter de grupos. El bot de Telegram levanta igual, así que
   desde afuera se ve un daemon sano con el puerto cerrado. Cae acá cualquier
   `broadcast:` en formato viejo (`port:` suelto o `remote:`, ver
   `broadcast-topology-config`), sin `auth`, o con un puerto fuera de 1024..65535.
3. **Un `broadcast:` fuera de `channels.telegram` se descartaba sin una palabra** — y
   este fue el caso real. El wiring solo lee `channels.telegram.broadcast`; un bloque
   en la raíz del agente lo tira `assemble_agent_config` (que solo copia `channels`),
   y uno en `channels.broadcast` sobrevive el filtro de adapters pero ningún canal lo
   consume. Peor que (2): el bloque **ni siquiera llega al parser**, así que tampoco
   se emite el error de topología. Silencio absoluto, en cualquier nivel de log.

**Cambios**:

1. `start()` hace el `bind()` **antes de crear la tarea** en rol server, y propaga el
   `OSError` al caller. La tarea de fondo (`_servir_para_siempre`) solo corre el
   `serve_forever()` sobre un socket ya abierto. Si el bind falla, el adapter queda
   con `_iniciado=False` — reintentable, y `stop()` sigue siendo no-op. El rol client
   NO cambia: su conexión es asíncrona por diseño (bucle de reconexión con backoff),
   así que `start()` no puede prometer upstream vivo.
2. `_tarea_principal` lleva un `add_done_callback` que loguea `ERROR` si muere con
   excepción. Cubre el transporte que revienta DESPUÉS del arranque.
3. `AppContainer.startup()` loguea el fallo con rol, host y puerto, diciendo que el
   puerto queda cerrado. El log de éxito ya no miente.
4. El parseo fallido de `channels.telegram` pasa de `WARNING` a `ERROR`, nombra los
   dos recursos que se pierden y enuncia la topología esperada.
5. `load_agent_config` avisa (`WARNING`) si encuentra un bloque `broadcast:` en la
   raíz del agente o en `channels.broadcast`, nombrando el único path que el wiring
   lee. No rompe el arranque: el agente carga igual.

Los mensajes accionables van con `%`-args y no solo en `extra=`: el formatter por
default de `journalctl` descarta el `extra`, y un log estructurado que el operador no
ve es lo mismo que no loguear.

**Sin migración de DB ni cambios de config. El wire format TCP no cambia.** Lo único
observable es que ahora hay `ERROR` en el log donde antes había silencio (o un
`WARNING` enterrado). **NUNCA dejar que el `bind()` de un puerto viva dentro de una
tarea de fondo mientras el caller loguea éxito**: un arranque que no puede fallar es
un arranque que no se puede diagnosticar. Y **NUNCA descartar en silencio un bloque
de config que el operador escribió**: si está en el lugar equivocado, decírselo es
más barato que la sesión de debugging que provoca.

---

### `search-history-retention-horizon`

El agente **confesó una mentira que no podía probar que había cometido**, y
escribió esa confesión en su memoria permanente. Caso real (2026-07-31,
`history.db` de producción, filas 6190-6219).

Cadena causal:

1. La consolidación llama `IHistoryStore.trim()`, que **BORRA filas** (`DELETE`,
   no un `LIMIT`). El historial NO es un registro completo del pasado.
2. Medido en producción: el scope `telegram:4879536` tenía **59 filas desde esa
   misma mañana** — 0.3 días de memoria. Los scopes sin filas de tool retenían
   40-60 días con las mismas ~64 filas (ver `trim-cuenta-conversacion`).
3. El usuario pregunta por unas tareas que pidió anotar ANTES de ese horizonte.
   El agente llama `search_history` cinco veces; las cinco devuelven
   `"No messages found ... for the given filters."` con **`success=True`**, y la
   `description` de la tool se vendía como *"the EXACT, verbatim text of messages
   from this agent's history database"* — el registro autoritativo del pasado.
4. **Nada le decía que el registro había sido truncado.** El agente no podía
   distinguir "nunca pasó" de "lo borré", y sus instrucciones (Honesty Above All,
   NO PHANTOM ACTIONS) lo empujan a admitir el fallo → convirtió la ausencia de
   evidencia en certeza: *"**La verdad**: te dije que las anoté, pero no lo hice"*.
5. Peor: escribió esa confesión en `users/telegram/{context_id}.md`, fichero
   permanente que se inyecta en cada turno y que la consolidación nunca toca.
   Auto-creencia envenenada, para siempre.

Es la MISMA falla que `outbound-send-single-owner` apuntando al pasado en vez de
al mundo: **afirmar sin evidencia**. Una confesión fantasma es tan grave como una
acción fantasma — y encima el usuario la creyó.

**Cambios**:

1. Port nuevo `IHistoryStore.retention_horizon(agent_id, channel, chat_id)` →
   `datetime | None`. Filtros con semántica de `search` (`None` = sin filtrar,
   NO `""`, que `_build_where_filters` trata como valor literal).
2. Devuelve el **MAX de los MIN por scope**, no el MIN global: `trim` borra por
   scope, así que cada conversación tiene su propio horizonte. Es el punto desde
   el cual la cobertura está COMPLETA en todo lo consultado. Con el MIN global la
   búsqueda real (sin `chat_id`) habría reportado *mayo* mientras el chat del
   usuario solo llegaba a esa mañana — y el agente habría concluido igual de mal.
3. `search_history` cierra **toda** respuesta —vacía o no— con la frase de
   alcance, y su `description` avisa que el historial está podado.

**Sin migración de DB ni cambios de config.** Solo cambia el texto que la tool
devuelve al LLM. NUNCA dejar que una tool responda "no existe" cuando lo que sabe
es "no lo tengo": una tool que no puede decir *no sé* fuerza al modelo a inventar
certeza.

---

### `trim-cuenta-conversacion`

`keep_last_messages` contaba **filas crudas**, así que el rastro protocolar
(`tool` results y el `assistant` que lleva `tool_calls`) consumía el presupuesto
de retención. Un turno con herramientas costaba como varios turnos de
conversación.

Medido en la `history.db` de producción (2026-07-31), con ~64 filas por scope:

| scope | filas | user | tool | memoria retenida |
|---|---|---|---|---|
| `telegram:4879536` (el chat más usado) | 59 | **10** | **19** | **0.3 días** |
| `telegram:1355413334` | 64 | 30 | 0 | 47.7 días |
| `telegram:39836839` | 56 | 29 | 0 | 60.0 días |

La correlación es exacta: **el agente con el que más se hablaba era el que menos
recordaba.** Y `outbound-send-single-owner` puso `persist_tool_calls` en `true`
por default, con lo que el efecto pasaba a ser universal.

**Cambio**: `SQLiteHistoryStore.trim` cuenta solo mensajes **conversacionales**
(`role IN ('user','assistant')` con `tool_calls IS NULL`). El corte es el id del
`keep_last`-ésimo conversacional más reciente de su scope; se borra todo lo
anterior. El rastro que queda del lado nuevo del corte sobrevive íntegro y no
paga presupuesto. Un scope con menos conversación que el presupuesto no tiene
corte (la subconsulta da `NULL`, `id < NULL` es `NULL`) → no se borra nada.

**Sin migración de DB ni cambios de config**, pero el **valor configurado cambia
de significado**: `keep_last_messages: 64` ahora son 64 mensajes de conversación
reales, no 64 filas. En una instancia con `persist_tool_calls` activo eso
**aumenta** lo que se retiene en disco — que es exactamente el punto. Si hace
falta acotar, bajar el número.

NUNCA expresar una política de retención en filas cuando lo que se quiere acotar
es conversación.

---

### `outbound-send-single-owner`

Un envío por `send_to_telegram` **desaparecía del contexto del LLM**, y con él la
tool call entera. Consecuencia real (2026-07-31, `history.db` de producción,
filas 6223-6237): el agente mandó un fichero, y dos turnos después lo **reenvió**;
cuando el usuario le reclamó, contestó *"no te lo he vuelto a enviar — el bloque
que ves es un artefacto del harness, no una acción mía"*. **No estaba
alucinando: el harness le había borrado su propia tool call.**

Cadena causal — **dos escritores** sobre `history.db` en el MISMO turno, sin
saber uno del otro:

1. Turno conversacional (`skip_marker=None`) → `incremental = True` → el tool
   loop persiste el rastro EN CALIENTE, mensaje a mensaje.
2. Se persiste `assistant`+`tool_calls[send_to_telegram]`.
3. La tool ejecuta y `TelegramChannelOutbound.send()` persistía **por su cuenta**
   un `Message(role=ASSISTANT, content=caption)` — DENTRO del grupo protocolar.
4. Se persiste el `tool` result.
   → En disco: `assistant(tool_calls)` → `assistant(texto)` → `tool`.
5. Al cargar, `_drop_orphan_tool_messages` solo toleraba `role=user` intercalado;
   ante un `assistant` hacía `break` → grupo "incompleto" → **descartaba el
   assistant+tool_calls**, y después el `tool` result quedaba huérfano
   top-level → **también descartado**. El envío entero, invisible.

`write_file` NO sufría esto: el bug era específico de las tools que envían por el
canal, las únicas que se auto-persisten.

**Cambios** (los tres son una sola decisión: **un solo dueño del rastro por
envío**, y un loader que no se rompe si aparece otro):

1. **Lector** — `_drop_orphan_tool_messages` reubica tras el grupo el `assistant`
   SIN tool_calls intercalado, igual que ya hacía con `user`. Un `assistant` CON
   tool_calls sigue cortando el escaneo (abre grupo nuevo). Defensa en
   profundidad: cualquier escritor concurrente futuro deja de poder borrar un
   grupo. **Recupera los rastros ya corruptos en disco** — no hace falta tocar
   `history.db`.
2. **Escritor** — `IChannelOutbound.send()` acepta `record_history: bool = True`.
   `send_to_telegram` (destino = chat del turno) pasa `record_history=not
   persist_tool_calls`: con el flag activo el tool loop YA es dueño (los
   argumentos del envío viajan en `tool_calls` y el resultado en el `tool`), así
   que el adapter no duplica. `send_telegram_message` (destino = OTRO chat)
   **siempre** persiste: el rastro del loop vive en el scope del turno y no llega
   al scope destino. El REST admin `/admin/send` (fuera de todo turno) idem.
3. **Default** — `chat_history.persist_tool_calls` pasa de `False` a **`True`**.

**Sin migración de DB.** Ninguna columna cambia de forma; el fix (1) sana en
lectura las filas ya escritas.

**Cambio de comportamiento observable por el flip del default**: una instancia que
no declaraba `persist_tool_calls` empieza a persistir el rastro de tools →
`history.db` crece más rápido y la ventana `max_messages` se llena con filas de
tool (acotadas por `persist_tool_result_max_chars`, default 2000). Si una
instancia necesita el comportamiento viejo, declarar `persist_tool_calls: false`
explícitamente. **Al apagarlo, `send_to_telegram` vuelve a delegar el registro en
el adapter** — el rastro no se pierde, cambia de dueño.

NUNCA volver a persistir en `history.db` desde dentro de una tool sin preguntarse
quién más está escribiendo ese scope en ese mismo turno. Y NUNCA asumir que el LLM
alucina cuando niega una acción propia: primero mirar qué le entregó el loader.

### `scheduler-trigger-type-mutable`

`trigger_type` y `task_kind` NO eran editables desde la tool `scheduler`
(`_MUTABLE_FIELDS` los omitía y `_update` los **descartaba en silencio**). No era
una decisión de diseño: el dominio SIEMPRE los soportó (`_INVALIDATING_FIELDS` en
`schedule_task.py` los incluye — ese frozenset solo tiene sentido si son
editables, y `update_task` re-valida el cron cuando cambia `task_kind`) y el CLI
SIEMPRE los dejó editar (`_EDITABLE_FIELDS`). Era un gap de UNA superficie,
heredado del commit inicial de la tool (`9ace581`), sin test ni comentario que lo
justificara. Peor: el `parameters_schema` **declaraba** `trigger_type` como
parámetro sin decir "create only" → el LLM lo intentaba y chocaba.

Tres síntomas según cómo llamara el LLM: (a) solo `trigger_type` → `updates`
vacío → `"No mutable fields provided"` (lo que reportaba el usuario); (b)
`trigger_type` + `trigger_payload` → el payload se validaba contra el trigger_type
**VIEJO** → `"Invalid trigger_payload for X"`, un error que **mentía sobre la
causa**; (c) los payload models no declaran `extra="forbid"` (default pydantic =
*ignore*) → si el payload nuevo casualmente validaba contra el modelo viejo, la
tool respondía `"updated"` y la task quedaba con el trigger_type viejo →
**corrupción silenciosa**.

**Cambios**:

1. **Invariante de dominio** — `ScheduledTask._trigger_type_matches_payload`
   (`@model_validator(mode="after")`) exige `trigger_payload.type ==
   trigger_type.value`. La unión es discriminada por `payload.type` pero el
   dispatcher rutea por la columna `trigger_type`: si divergen, la task ejecuta
   un trigger distinto del que declara. Aplica a TODAS las superficies (tool,
   CLI, repo) — el estado incoherente deja de ser representable.
2. **Tool**: `trigger_type` y `task_kind` entran a `_MUTABLE_FIELDS`, pero como
   cambio **ATÓMICO** junto a su campo acoplado (`_COUPLED_FIELDS`):
   `trigger_type` exige `trigger_payload`, `task_kind` exige `schedule`. Falta el
   acompañante → error accionable que lo NOMBRA (con ejemplo de payload / formato
   de schedule esperado), nunca un drop silencioso.
3. El payload se valida contra el trigger_type **EFECTIVO** (el nuevo si viene en
   la llamada, el existente si no); ídem el schedule contra el `task_kind`
   efectivo (cron ↔ ISO). Mandar el valor ACTUAL de `trigger_type`/`task_kind` es
   un **no-op**, no un error: el LLM suele re-enviar lo que leyó con `get`.
4. Al **cambiar a** `channel_send` no hay target previo que heredar — el `assert
   isinstance(existing.trigger_payload, ChannelSendPayload)` habría reventado.
   Ahora cae a la conversación actual (misma lógica que `_create`).

**Sin migración de DB ni cambios de config.** Ninguna columna cambia de forma.
**Chequeo previo al deploy**: el validador nuevo corre también al LEER
(`_row_to_task`), así que una fila legacy incoherente (posible: el editor del CLI
la permitía) haría fallar el listado de tareas. Verificar en cada instancia antes
de reiniciar:

```bash
sqlite3 ~/.inaki/scheduler.db "SELECT id, name, trigger_type, json_extract(trigger_payload,'\$.type') FROM scheduled_tasks WHERE trigger_type IS NOT json_extract(trigger_payload,'\$.type');"
```

Salida vacía = todo coherente. (Verificado el 2026-07-26 en el home local: 18
tareas, cero incoherentes.) Si aparece alguna, corregir el `trigger_payload` con
`inaki scheduler edit <ID>` ANTES de desplegar.

NUNCA volver a hacer que `_update` descarte campos en silencio: si un campo no se
puede cambiar, el error tiene que decirlo. Y NUNCA validar un `trigger_payload`
contra un `trigger_type` que la misma llamada está cambiando.

### `write-file-explicit-mode`

`write_file` tenía `overwrite: bool = False` y ese default **appendeaba**. Cuando el
LLM quería actualizar un fichero, mandaba el contenido completo sin tocar el flag y
el tool le pegaba el texto nuevo al final del viejo → **contenido duplicado**. La
descripción llamaba a ese modo *"safe mode"*, que era exactamente al revés. El LLM
no elegía mal por falta de instrucciones: la tool producía el bug por diseño, y
además `patch_file` era el camino CARO (pide líneas 1-based y `read_file` devolvía
el contenido sin numerar → había que contar a mano). Entre una tool barata que
rompe y una cara que funciona, un LLM agarra la barata. Siempre.

**Cambios** (los tres son una sola decisión: hacer que el camino correcto sea el barato):

1. **`write_file`: `overwrite: bool` → `mode: "create" | "overwrite" | "append"`, SIN
   default.** Sobre un fichero existente y no vacío el modo es **obligatorio**: si
   falta, la tool **falla sin escribir** y el error nombra la alternativa
   (`edit_file` / `patch_file`). Si el target no existe o está vacío, `mode` ausente
   = `create`. `mode="create"` sobre fichero con contenido también falla.
2. **`read_file`: flag opt-in `line_numbers: bool = False`.** Con `true` cada línea
   sale prefijada `"  42\ttexto"`, **numeración absoluta** (respeta `offset`) para
   que los números se pasen tal cual a `patch_file`. El payload suma la clave
   `line_numbers`. Default `false` → output idéntico al anterior.
3. **Descripciones cruzadas**: `write_file` dice explícitamente que NO puede
   modificar contenido in place y deriva a `edit_file`/`patch_file`; `edit_file` se
   declara la tool **preferida** para actualizar; `patch_file` manda a leer con
   `line_numbers=true` antes de patchear. Las tres se nombran entre sí — sin eso el
   flag de (2) es invisible para el LLM.

**BREAKING del contrato de la tool (sin auto-migración)**: el parámetro `overwrite`
**ya no existe**. Si llega, la tool devuelve error explícito y **no escribe** — corte
limpio deliberado: mapearlo en silencio a un modo reintroduce el fallo que esto
elimina. `overwrite=True` → `mode="overwrite"`; `overwrite=False` → `mode="append"`
(o, casi siempre, la llamada correcta era `edit_file`).

**Sin migración de DB ni cambios de config.** Nada persistido cambia de forma. Las
tool calls viejas viven en `history.db` pero no se re-ejecutan. NUNCA volver a darle
a `write_file` un modo por default sobre ficheros con contenido: el default ES el bug.

### `persist-tool-calls`

Por diseño histórico, el rastro de tool calls de un turno (el mensaje
`assistant` con `tool_calls` + los `tool` results) vivía SOLO en el
`working_messages` del tool loop y se **descartaba** al terminar — a `history.db`
llegaban únicamente `user`/`assistant` de texto. Consecuencia real reportada por
el usuario: el agente escribía una investigación con `write_file`, y un par de
turnos después **no tenía registro de en qué path la guardó** → recomenzaba de
cero. Se generaliza: el agente era amnésico de TODA su actividad con
herramientas entre turnos (web_search, read_file, RAG, etc.).

**Fix**: flag **global** `chat_history.persist_tool_calls` (default `False`).
Con `True`, el agente principal persiste el par protocolar completo
(assistant+tool_calls ↔ tool results) y recupera **memoria episódica de sus
acciones**. Descartado el enfoque original (persistir la cadena de pensamiento):
el problema no era el reasoning sino el tool result perdido. `thinking` queda
FUERA (Anthropic exige `signature` para reconstruirlo del historial y el dominio
no la modela).

**Por qué global y no per-tool**: el protocolo (OpenAI y Anthropic) exige que
cada `tool_call_id` de un `assistant` tenga su `tool` result emparejado, o el
provider tira **400**. La unidad atómica es el GRUPO (assistant+tool_calls +
TODOS sus results): no se puede persistir uno y descartar otro. Un flag per-tool
rompería el pairing en batches mixtos. El costo se acota con truncación, no con
selección per-tool.

**Componentes**:
- `chat_history.persist_tool_calls: bool` y `persist_tool_result_max_chars: int`
  (default 2000; `0` = sin truncar) → `RunAgentSettings` (VO del use case, NO del
  store: decidir/truncar es semántica del turno) vía `build_run_agent_settings`.
- `history.db`: columnas nuevas `tool_calls TEXT` (JSON), `tool_call_id TEXT`.
  Migración **en caliente** idempotente (`_ensure_history_columns`, ALTER TABLE
  ADD COLUMN vía `PRAGMA table_info`, patrón de `_ensure_agent_state_schema`).
  **Sin borrar la DB**: filas viejas quedan con `NULL` (mensajes de texto).
- `append` acepta `Role.TOOL` (además de USER/ASSISTANT); `SYSTEM`/`TOOL_RESULT`
  siguen ignorados.
- **Windowing group-aware** (`_drop_orphan_tool_messages`, aplicado en `load`):
  cuando la ventana `max_messages` (o un `trim`) deja un `tool` result sin su
  `assistant`, ese huérfano se descarta ANTES de mandarlo al provider — es LA
  garantía contra el 400. Escaneo lineal que cubre huérfanos al inicio y en el
  medio. No-op cuando no hay mensajes `tool` (flag off). `trim` no se tocó: si
  deja un huérfano, `load` lo filtra.
- `search` oculta `role=tool` salvo que se pida `role="tool"` explícito; y
  `RunAgentUseCase.get_history` (vista humana REST/CLI) filtra los `tool`.
- **Tool loop**: `run_tool_loop` recibe un acumulador opcional `tool_trace`
  (mismo patrón que `RecordingIntermediateSink`); el caller (`RunAgentUseCase`)
  es dueño de la lista y la persiste tras el loop (queda completa aun si corta
  por `ToolLoopMaxIterationsError`). El one-shot (subagentes) pasa `None` → sin
  persistencia, por eso el feature es **solo del agente principal, gratis**.

**Reconciliación con `intermediate-persist`**: el mensaje assistant+tool_calls YA
lleva la narración en su `content`, así que con el flag ON se persiste el
`tool_trace` y NO además `recording_sink.messages` (evita duplicar la narración);
con el flag OFF, comportamiento legacy intacto (narración como texto plano).

**Truncación**: `persist_tool_result_max_chars` recorta SOLO la copia persistida
del tool result (marcador `…[truncado]`); el turno en curso siempre ve el output
completo. Acota contexto de turnos futuros y disco (web_search/RAG dumps).

**Sin cambios de config obligatorios.** Default `False` → comportamiento idéntico
al actual. Backward-compat total. NUNCA volver al enfoque per-tool ni persistir
`thinking` sin resolver antes el problema de la `signature` de Anthropic.

### `attachment-grammar`

Los 4 dialectos ad-hoc de media en `history.db` (`__PHOTO__ <caption>`,
`__ALBUM__ (N photos):`, `__FILE__/__VIDEO__ <name> at <path>`, y el audio que
persistía la transcripción plana SIN marcador) se reemplazan por **una gramática
unificada de attachments** definida UNA vez en
`core/domain/value_objects/attachment.py` (`IncomingAttachment` +
`format_attachment`/`format_album`) y explicada al LLM por la sección estática
`ATTACHMENTS_SECTION` (`_turn_pipeline.py`, siempre inyectada, como la de
in-flight):

```
@photo at /abs/path.jpg                       ← línea principal: tipo + path local
@audio voz.ogg (audio/ogg) at /abs/path.ogg
@file informe.pdf (application/pdf) at /abs/path.pdf
@album (8 items):                             ← + una línea @<tipo> por miembro
@transcription: ... / @analysis: ... / @caption: ...   ← auxiliares, orden fijo
@audio pending (id: X) — retrieve with download_from_telegram  ← modo degradado
```

El token accionable es el **path local** (pre-descarga obligatoria para todos los
tipos; los bytes ya en memoria — fotos, audios — se escriben directo al cache del
workspace vía `_save_bytes_to_workspace`, sin segunda descarga). El
`telegram_file_id` NUNCA va al historial: es transporte y vive en
`telegram_files.db`. `file_unique_id` = basename del path.

**Principio nuevo — persistencia simétrica**: TODO media deja su bloque en
`history.db`, con o sin caption, dispare o no turno. Cierra tres agujeros reales:
(1) documento sin caption era 100% invisible para el LLM (bug del "audio viejo":
mp3 enviado como document → `_handle_silent_media` silencioso → el LLM adivinaba
con `download_from_telegram(content_type=audio)` y traía OTRO audio); (2) las
salidas tempranas de voz (disabled/too-large/transcripción fallida) no dejaban
rastro; (3) en grupos, `_handle_group_message` DESCARTABA el `user_input`
pre-formateado del media y persistía `format_group_message(update.message)` →
`"marta said: "` vacío (fix: param `preformatted=True`). Decisión del usuario:
depósito sin caption = persistir SIN disparar turno (cero tokens; el bloque queda
visible para el próximo turno).

**Otros cambios**: (a) document con mime `audio/*` rutea al pipeline de voz
(`_extract_file_metadata` y `extract_audio_payload` lo tratan como audio); (b) la
ventana FIJA de álbum (`ALBUM_GATHER_DELAY_SEC=2s` desde el PRIMER miembro, bug
7-de-8-fotos) se reemplaza por **debounce por `media_group_id`**
(`ALBUM_DEBOUNCE_SEC=1.5s` desde el ÚLTIMO; cada miembro resetea el timer), y se
generaliza a documentos/videos enviados juntos (Telegram también les pone
`media_group_id`); un miembro tardío post-flush persiste su bloque sin re-turno
(`_record_straggler`); (c) port nuevo `IFileRecordRepo.query_by_media_group`
(todos los tipos, `received_at ASC`); (d) la description de
`download_from_telegram` ahora dice "usá el path del bloque; llamame solo ante
`pending` o para media viejo".

**Extensión `format_analysis_delta` (2026-08-01)** — análisis TARDÍO de un
attachment ya persistido:

```
@analysis (for /abs/path.jpg): se ve a Alberto en una terraza...
```

Motivo: en el camino in-flight de foto (llegó una foto con un turno ya corriendo
en ese scope), `media.py` persistía el placeholder `@photo` al principio
—obligatorio: `ProcessPhotoUseCase` necesita el `history_id` para la side-table
`message_face_metadata`, y la persistencia simétrica exige dejar rastro— y
después persistía el bloque **enriquecido completo** como fila nueva. Las dos
filas tienen `id > cursor`, así que **el drain devolvía las dos**: el LLM veía la
misma foto dos veces (una sin `@analysis`, otra con) y el duplicado quedaba en
`history.db` para siempre. El camino normal ya evitaba exactamente eso con
`update_message_content`.

La fila nueva sigue siendo necesaria (el drain busca `id > cursor`, NO detecta
ediciones in-place), pero ahora lleva **solo el delta**: referencia al bloque
original por su `ref_token()` (el path local, o `id: <file_ref>` en modo
degradado) y aporta únicamente el análisis. Sin caption — ya viaja en el bloque
original; duplicarla reintroduciría el problema por otra puerta. Sin análisis no
hay segunda fila. `ATTACHMENTS_SECTION` explica la forma al LLM, incluyendo que
**no** significa que llegó media nueva.

Trade-off aceptado: en `history.db` el análisis queda en una fila separada de la
foto. Si la ventana `max_messages` corta entre ambas, sobrevive un `@analysis
(for /p)` sin su bloque — el path sigue siendo accionable.

**Sin migración de DB ni cambios de config.** Las filas viejas con prefijos
`__X__` conviven con las nuevas (el LLM ve historia mixta unos días — aceptable).
NUNCA volver a inventar un formato de persistencia por tipo de media o por canal:
la gramática se extiende en `attachment.py` o no se extiende.

### `groups-vs-broadcast`

La **política de respuesta en grupos** (`behavior`, `bot_username`, `rate_limiter`,
`rate_limiter_window`) se movió de `channels.telegram.broadcast` a
`channels.telegram.groups`. Razón: esos campos describen *cómo responde el bot en un
grupo* — aplican con o sin broadcast TCP —, pero vivían en `BroadcastConfig`, cuyo
validador exige `port XOR remote`. Eso **obligaba a levantar el transporte LAN solo
para configurar el comportamiento**: no podías tener `behavior: mention` en un bot
sin broadcast. Ahora `groups` cubre timing (`min/max_delay_response`, `reactions`) +
política, y `BroadcastConfig` queda como **transporte puro** (`port`/`remote`/`auth`/`emit`;
la forma de esos campos fue rediseñada después — ver `broadcast-topology-config`).

**Cambio de wiring clave**: el `FixedWindowRateLimiter` ya **no** se instancia dentro
de `_wire_broadcast_for_agent` atado a la presencia de `broadcast:`. Ahora se crea cuando
`groups.behavior == "autonomous"` (independiente de broadcast) y se guarda en
`AgentContainer.group_rate_limiter` (renombrado desde `broadcast_rate_limiter`). Un bot
autónomo sin LAN ahora tiene rate limiting real — antes el limiter quedaba en `None`.
El `group_flow` y el receiver bot-to-bot comparten esa misma instancia.

**Migración automática en caliente** (`migrate_telegram_group_fields` en `config_loader.py`,
llamada desde `ensure_user_config` ANTES de cargar la config, por eso surte efecto en la
sesión actual): al arrancar, mueve los 4 campos de `broadcast` a `groups` en `global.yaml`,
`global.secrets.yaml` y todos los `agents/*.yaml` (ruamel, preserva comentarios). Idempotente;
`groups` gana ante conflicto; si `broadcast` queda sin transporte tras mover, se elimina el
bloque (no dispara el validador port-XOR-remote). El setup TUI deriva los campos por
introspección del schema → `behavior` aparece solo bajo la sección GROUPS, sin tocar el TUI.

**Sin migración de DB ni pasos manuales del operador.** Backward-compat: configs viejas se
migran solas. El corte es limpio (el bot lee SOLO de `groups`, sin fallback a `broadcast`):
si por un path raro la migración no corriera, un bot caería al default `behavior: mention`.

### `broadcast-topology-config`

La config de `channels.telegram.broadcast` abandonó el formato **implícito** (`port`
presente = server, `remote` presente = client, `auth` duplicado en dos paths según el
rol, `remote.host` como string `"ip:port"` parseado a mano en el container con fallo
silencioso) por un formato de **rol explícito**:

```yaml
broadcast:
  enabled: true            # kill-switch (default true); false relaja topología y auth
  auth: "shared-secret"    # HMAC único para AMBOS roles (client debe matchear server)
  server: { port: 6499 }   # XOR client — el rol se LEE en el YAML
  client: { host: "192.168.1.50", port: 6499 }
  emit: { ... }            # sin cambios
```

Modelos nuevos `BroadcastServerConfig` / `BroadcastClientConfig` (rangos de puerto
1024..65535 vía `Field(ge/le)`, validados al CARGAR — muere el parseo de `"ip:port"` y
sus dos paths de skip silencioso en `_wire_broadcast_for_agent`). `RemoteBroadcastConfig`
eliminado. El validador pasa de `port XOR remote` a `server XOR client` + `auth`
obligatorio, y con `enabled: false` no exige nada (el bloque puede quedar incompleto
mientras está apagado). `_validate_channel_uniqueness` chequea `server.port` (ignora
bloques `enabled: false` — no hacen `bind()`).

**Sin migración automática**: todas las instancias del operador se migraron a mano al
formato nuevo, así que NO se dejó una `migrate_*` en `config_loader.py` (sería código
muerto — el corte fue manual, cerrado). Si en el futuro reaparece config vieja en una
instancia, el mapeo es mecánico: `port` → `server.port`; `remote.host "ip:port"` →
`client.host`+`client.port`; `remote.auth` → `auth`.

**Sin migración de DB. El wire format TCP no cambia** — esto es SOLO config; el cambio
no toca la red. Corte limpio: el código lee SOLO el formato nuevo, sin fallback — un
bloque viejo sin migrar falla al cargar con error de validación explícito. NUNCA volver
al rol implícito por presencia de campo ni duplicar `auth` por rol.

### `channel-send-history-persist`

El trigger `channel_send` ahora **persiste el texto enviado en el historial**
(`history.db`) del agente dueño, no solo en `task_logs.metadata`. Antes era el
único canal por el que el asistente "hablaba" sin dejar rastro en su propia
conversación — asimetría con `agent_send`, que ya persistía su intercambio vía
`llm_dispatcher`. Si el usuario respondía a un `channel_send`, el agente no
tenía contexto de lo que había mandado.

**Cuándo persiste**: solo si el `resolved_target` (tras la cascada del router)
apunta a un **canal conversacional vivo** — su prefijo está entre los sinks
nativos (`native_sinks`, hoy `{telegram}`) — **y** hay un agente dueño. El dueño
se resuelve `payload.agent_id or task.created_by`: por default es quien agendó la
tarea (`created_by`), pero un **`agent_id` explícito en el `ChannelSendPayload`
permite publicar EN NOMBRE DE otro agente** (un cronista dedicado que manda como
`anacleto` para que el agente conversacional conserve el contexto cuando le
respondan). Se persiste un `Message(role=ASSISTANT)` en el scope
`(dueño, channel, chat_id)` parseado del `resolved_target` (donde el usuario
REALMENTE vio el mensaje, no el target original). **Cuándo NO**: cayó al fallback
de archivo (no es canal real), no hay agente dueño (origen CLI sin `created_by` ni
`agent_id`), o corrida manual (`run_task_now`/`ephemeral=True`, para no ensuciar la
conversación real al testear). `created_by` sigue siendo siempre quien agendó
(hard-injected) — el `agent_id` solo redirige el HISTORIAL, no el bookkeeping.

**Componentes nuevos**:
- `IChannelHistoryRecorder` (port en `scheduler_dispatch_port.py`) — campo nuevo
  en `SchedulerDispatchPorts`. El `SchedulerService` solo delega; el recorder es
  el único que conoce qué canales son conversacionales y cómo resolver el
  historial por `agent_id`.
- `ChannelHistoryRecorderAdapter` (`adapters/outbound/scheduler/dispatch_adapters.py`)
  — sigue el patrón de `LLMDispatcherAdapter`: recibe el dict de agentes
  duck-typed (`adapters` no importa `infrastructure`) y resuelve `agent.history`
  por id. Recibe el set de canales conversacionales (= `set(native_sinks)`).
- `AgentContainer.history` (property pública nueva) para que el recorder acceda
  al `SQLiteHistoryStore` del agente, igual que `run_agent`.

**Sin migración de DB ni cambios de config**. La columna `(channel, chat_id)` de
`history.db` ya existía. El mensaje persistido fluye por memoria como cualquier
otro `assistant` message (se consolidará en su scope). Backward-compat: tareas
sin `created_by` (CLI) y canales no nativos se comportan igual que antes. El campo
`ChannelSendPayload.agent_id` es **opcional** (ausente → cae a `created_by`), así
que configs y tareas existentes no cambian de comportamiento. Aviso de seguridad:
con `agent_id` libre, cualquier agente puede escribir en el historial de otro —
aceptable para uso doméstico; si se abre a terceros, validar que el `agent_id` sea
un destino permitido.

### `subagent-inheritance`

El flujo `delegate` dejó de ejecutar el `run_agent_one_shot` pre-built del
sub-agente (que corría con la config resuelta contra `global`). Ahora cada
delegación construye una **instancia efímera resuelta contra el CALLER**
(`AgentContainer.build_ephemeral_child`): el hijo hereda el `llm` del padre por
default (primitivo `inherit` + `SUBAGENT_DEFAULTS`), opera con las tools/recursos
del padre (`caller._tools`), y puede acotar el subset visible con el campo nuevo
`tools.allowed`. La misma definición de sub delegada por P y por Q hereda LLMs
distintos (per-caller, no per-definición).

**Sin migración de DB ni cambios de config obligatorios.** Es 100% in-memory y
backward-compat para configs existentes: un sub sin `tools.allowed` ve todo el
toolkit del caller; sin override de `llm` hereda la instancia del padre. El campo
`tools.allowed` (lista de nombres; `None`/ausente = sin restricción) SOLO tiene
efecto en el flujo `delegate` (one-shot sin RAG) — en el turno normal es inerte.

**Behavior shift observable**: un sub-agente que antes corría con el `llm` /
`workspace` declarados en SU YAML (resueltos contra `global`) ahora hereda los del
caller. Si un sub necesita un `llm` propio, debe declararlo en su delta (override)
→ se construye vía `LLMProviderFactory` con los `providers` (credenciales)
heredados del caller. El `run_agent_one_shot` pre-built de cada container sigue
existiendo pero ya NO se usa en el path `delegate`.

### `tool-config-own-file`

El store del Tool Config Protocol dejó de vivir dentro de `global.secrets.yaml`
y pasó a su **propio archivo daemon-owned**: `config/tool_config.yaml`. Razón:
`global.secrets.yaml` lo escribe el operador a mano (api keys de providers,
tokens) pero el daemon le reescribía el bloque `tool_config:` en runtime — dos
dueños en un archivo, dolor para quien despliega. Ahora el operador recupera
`global.secrets.yaml` como archivo de SOLO credenciales que el daemon no toca.

**Fix de bug incluido**: `load_global_config` construía `GlobalConfig` SIN pasar
`tool_config=`, así que `global_config.tool_config` salía SIEMPRE `{}` y el store
(que se sembraba de ahí vía `initial=`) nunca leía el disco al arrancar → tras
cualquier reinicio del daemon TODA la config de tools (exchange, web_search…) era
invisible en memoria hasta reconfigurar. Se resuelve por diseño: el store ahora
**lee su propio `tool_config.yaml`** en `__init__` (sin `initial`, sin depender
del loader). El campo `GlobalConfig.tool_config` se eliminó (estaba muerto).

**Migración automática en caliente** (`migrate_tool_config_to_own_file` en
`config_loader.py`, llamada desde `ensure_user_config` y desde
`AppContainer._init_shared_state`): al arrancar, si existe el bloque `tool_config:`
en `global.secrets.yaml` y no existe `config/tool_config.yaml`, mueve el bloque al
archivo nuevo y lo limpia del secrets (preserva el resto + comentarios, ruamel).
Orden seguro: escribe el archivo nuevo ANTES de limpiar el viejo — peor caso,
duplicado benigno (el store solo lee `tool_config.yaml`), nunca pérdida. La
`secret.key` NO cambia → los `enc:` siguen descifrándose, sin reconfigurar.

**Sin pasos manuales del operador.** Si se desea, tras verificar que
`config/tool_config.yaml` quedó poblado, el bloque viejo ya no está en
`global.secrets.yaml`. No tocar el `secrets_path` del store de vuelta a
`global.secrets.yaml`: el archivo propio es la decisión.

### `tool-config-protocol`

Se eliminaron las 4 islas de configuración per-tool (`web_search_config.yaml`,
`exchange_config.yaml`, `fal_music_config.yaml`, `replicate_music_config.yaml` —
cada una con su propio YAML + `CryptoService`), `CryptoService` mismo (Fernet +
`~/.inaki/.env`, único habitante de `core/services/`), el wizard
`inaki setup secret-key` (`setup_wizard.py`) y la dependencia `python-dotenv`.
El wizard escribía la clave en `{repo}/.env` mientras `CryptoService` la leía
de `~/.inaki/.env` — nunca fueron el mismo archivo.

Reemplazo: **Tool Config Protocol** (`core/ports/outbound/tool_config_port.py`,
`IToolConfigStore` sync). La función de configurar credenciales conversando con
el agente (`operation=configure` / `show_config`) se PRESERVA — lo que cambia
es el storage: todo va al bloque `tool_config.{namespace}` de
`global.secrets.yaml` (sistema de 4 capas), con campos sensibles cifrados
(Fernet, prefijo `enc:`, clave auto-generada en `~/.inaki/secret.key` 0600).
`YamlToolConfigStore` (adapters — `cryptography` NO vuelve al core) escribe con
ruamel preservando comentarios; los writes son efectivos al instante sin
reiniciar. Una tool adopta el protocolo declarando `config_namespace` (class
attr de `ITool`) — el container la instancia con `config_store=...` (aplica
también a tools de `ext/`, cuyo contrato deja de ser estrictamente zero-arg).
Ver `docs/configuracion.md` → "Tool Config Protocol".

**Pasos del operador**: borrar los archivos huérfanos
(`~/.inaki/config/{web_search,exchange,fal_music,replicate_music}_config.yaml`,
`~/.inaki/.env`). Las credenciales viejas cifradas no son recuperables — basta
decirle la key al agente por chat (ej: "configurá web_search con la key tvly-...")
o escribirla a mano bajo `tool_config:` en `global.secrets.yaml`.
`DEUDA_TERCEROS_CORE` en `test_architecture.py` quedó vacía y debe mantenerse así.

### `drop-per-agent-rest`

La superficie REST per-agente (`channels.rest`: un puerto uvicorn por agente,
auth `X-API-Key`) se eliminó. Toda la superficie HTTP vive en el **admin server**
(un puerto global, ruteo por `agent_id`, auth `X-Admin-Key`). Equivalencias:

| Per-agente (eliminado) | Admin server |
|---|---|
| `POST /chat` | `POST /admin/chat/turn` |
| `GET /info` | `GET /admin/agent/info?agent_id=X` |
| `GET /history` / `DELETE /history` | `GET`/`DELETE /admin/chat/history?agent_id=X` |
| `POST /consolidate` | `POST /consolidate` con body `{"agent_id": "X"}` (sin agent_id consolida todos) |

Bloques `channels.rest` en YAML existentes se ignoran silenciosamente (el dict
de channels admite claves arbitrarias). La validación de colisión de puertos
REST entre agentes se eliminó de `config.py` junto con la superficie.

### `multi-agent-telegram-broadcast`

The `history` table was extended with native `channel` and `chat_id` columns. No
auto-migration exists — the DB must be dropped and rebuilt.

Operator steps: stop daemon → `rm ~/.inaki/data/history.db ~/.inaki/data/inaki.db` → add
`channels.telegram.broadcast` config (optional) → restart. See `docs/broadcast-smoke.md`
for the full bootstrap walkthrough.

### `telegram-photo-recognition`

La tabla `message_face_metadata` se agrega como side-table en `history.db`. No hay
auto-migración — la DB debe borrarse y reconstruirse.

Pasos del operador: detener daemon → `rm ~/.inaki/data/history.db ~/.inaki/data/inaki.db` →
agregar bloque `photos:` en `~/.inaki/config/global.yaml` (o dejar `photos: null` para
desactivar) → reiniciar. La DB `faces.db` se crea automáticamente al primer uso.

**Cambio de modelo facial** (`faces.model`): invalida `faces.db` → borrar
`~/.inaki/data/faces.db` y re-enrolar todas las personas. Ver `docs/face-recognition.md`.

### `broadcast-cross-agent-events`

El wire format del broadcast TCP cambió: el `BroadcastMessage` ahora carga `event_type`
(Literal de 3 valores), `sender` y `content` (renombre desde `message`). El HMAC canonical
incluye los nuevos campos, por lo que **versiones viejas y nuevas no son compatibles** —
mensajes con formato distinto se descartan por mismatch silenciosamente.

**Pasos del operador**: detener el daemon en TODOS los Pis del LAN broadcast
simultáneamente → actualizar código → reiniciar. No hay migración de DB. Si un solo Pi
queda atrás, los broadcasts entre él y los actualizados se pierden silenciosamente
(visible en logs como `broadcast.message.dropped.hmac_mismatch`).

**Nuevos flags `broadcast.emit.*`**: defaults backward-compat (`assistant_response=true`,
otros `false`) — sin cambios en config existente, comportamiento idéntico al previo. Para
broadcastear transcripciones de voice o descripciones de fotos, activar `user_input_voice`
y/o `user_input_photo` en UN bot del grupo (ver `docs/configuracion.md`).

### `agent-state-scoped-by-channel-chat`

La tabla `agent_state` en `history.db` pasa de PK `agent_id` a PK compuesta
`(agent_id, channel, chat_id)` y agrega columna `updated_at` para purga futura.
Esto elimina el bleed de sticky skills/tools entre conversaciones distintas del
mismo agente (ej: un grupo de Telegram vs un chat privado ya no comparten estado).

La migración es **automática en caliente** — `_ensure_agent_state_schema()` detecta
el schema legacy en el primer arranque post-deploy y migra los registros existentes
al scope `(agent_id, '', '')` sin pérdida de datos. No se requiere intervención manual.

`save_state` y `load_state` ahora aceptan `channel` y `chat_id` (default `""`).
`clear(channel, chat_id)` borra también el `agent_state` del scope limpiado
(antes solo borraba el historial scoped y dejaba el state intacto).

### `memory-management-tools`

Se exponen al LLM tres tools nuevas (`search_memory`, `delete_memory`,
`update_memory`) y se añaden los métodos `IMemoryRepository.delete()` y
`update()` con soft-delete reversible. Resuelve el caso "borrá esa memoria
errónea" que antes el agente no podía cumplir.

`memories` recibe una columna `deleted INTEGER NOT NULL DEFAULT 0` y el índice
de scope se reescribe como **partial index** sobre `deleted = 0` (más compacto:
solo indexa entries activas). `search`, `search_with_scores` y `get_recent`
filtran `deleted = 0` automáticamente; el `update` y el `delete` operan solo
sobre entries activas (no se permite editar o re-borrar una soft-deleted).

Migración en caliente:

```bash
sqlite3 ~/.inaki/data/inaki.db <<'SQL'
ALTER TABLE memories ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0;
DROP INDEX IF EXISTS idx_memories_scope;
CREATE INDEX idx_memories_scope
  ON memories(agent_id, channel, chat_id, created_at DESC)
  WHERE deleted = 0;
SQL
```

**Bug fix en `store`/`update`**: la tabla virtual `vec0` (`memory_embeddings`)
NO soporta `INSERT OR REPLACE` — el path REPLACE rompe con UNIQUE
constraint. Se reemplaza por `DELETE` + `INSERT`. Esto siempre fue un latent
bug en `store` cuando el mismo id se reescribía.

### `memory-scoped-by-channel-chat`

La tabla `memories` se extiende con columnas `channel TEXT` y `chat_id TEXT` (ambas
nullable) más un índice `(agent_id, channel, chat_id, created_at DESC)`. Cada
`MemoryEntry` extraído ahora se persiste con el scope de la conversación de origen y
el digest markdown se aísla por scope (`mem/digest_{channel}_{chat_id}.md`). Esto evita
que recuerdos de un grupo de Telegram se filtren a un chat privado del mismo agente.

A diferencia de las migraciones previas, **no hace falta borrar `inaki.db`** —
las filas existentes quedan con `channel = NULL` y `chat_id = NULL` (recuerdos
"globales" pre-migración) y siguen siendo recuperables por `search`. Se migra en
caliente con `ALTER TABLE`:

```bash
sqlite3 ~/.inaki/data/inaki.db <<'SQL'
ALTER TABLE memories ADD COLUMN channel TEXT;
ALTER TABLE memories ADD COLUMN chat_id TEXT;
CREATE INDEX IF NOT EXISTS idx_memories_scope
  ON memories(agent_id, channel, chat_id, created_at DESC);
SQL
```

**Cambios de config**: el default de `memories.digest_filename` pasa de
`mem/last_memories.md` a `mem/digest_{channel}_{chat_id}.md`. Si tenés un override
explícito en tu YAML, actualizalo al template — sin placeholders el sistema vuelve a
escribir un único archivo (comportamiento legacy, recuerdos cruzados).

**Cambio semántico de `memories.consolidation.delay_seconds`**: ahora también se respeta entre
scopes `(channel, chat_id)` dentro del mismo agente, no solo entre agentes en la
consolidación global.

### `background-delegation`

La tool `delegate` ahora es **async por defecto**. El parámetro `wait` controla
el modo: `wait=true` preserva el comportamiento sincrónico legacy
(bloquea hasta que el hijo responde con DelegationResult parseado); `wait=false`
(default) encola la delegación en una cola in-memory bajo un semáforo de 3 y
devuelve `bg-N` al instante. Cuando la delegación termina, el resultado se
inyecta en el `(channel, chat_id)` original via `LLMDispatcherAdapter.dispatch`
como un mensaje `Role.USER` con prefijo `[bg-N] ...`. El agente padre tiene una
sección del system prompt (en inglés) que le explica cómo procesar esos
mensajes — sin saludo, sin preámbulo.

**FIX bg-result-delivery (2026-07-12)**: el turno que procesa el `[bg-N]` era
*headless* — `_dispatch_result` descartaba el return del `dispatch()` (la
respuesta digerida del padre) y no pasaba `intermediate_sink`. La respuesta
quedaba en `history.db` pero JAMÁS llegaba al canal: el usuario esperaba un
anuncio que no veía mientras el agente creía habérselo dado. Lo "aceptado como
diseño" (Defecto 2, ver `intermediate-persist`) era no entregar el JSON CRUDO
del sub — nunca tragarse también el digest del padre. Ahora la cola recibe
`result_sender` (el `ChannelRouter` vía el port `IChannelSender`) +
`conversational_channels` (= sinks nativos): si el scope original es un canal
vivo, la respuesta se envía a `channel:chat_id`, la narración intermedia fluye
en vivo por `build_intermediate_sink`, y el turno recibe `skip_marker=__SKIP__`
(la sección in-flight del prompt ahora explica que la respuesta llega al
usuario y que puede optar por silencio deliberado). La entrega es best-effort:
un fallo del send NO reintenta el dispatch — re-correría el turno completo del
LLM y duplicaría historial; se loguea y la respuesta queda en el historial. El
`ChannelRouter` se construye en `AppContainer._build_channel_router()` ANTES de
la cola (antes nacía dentro de `_build_scheduler`, demasiado tarde). Origen no
conversacional (CLI/REST sin canal vivo) o respuesta vacía → comportamiento
anterior (solo historial). NUNCA volver a descartar el return del dispatch: es
la única vía por la que el anuncio de un bg-result llega al usuario.

**Sin migración de DB ni cambios de config**. El feature es 100% in-memory: si
el daemon reinicia con tasks in-flight, se pierden silenciosamente (decisión
explícita para uso doméstico Pi 5 — sin retries ni persistencia).

**Lock-per-scope en `LLMDispatcherAdapter`**: la misma instancia se comparte
entre `BackgroundDelegationQueueAdapter` y `SchedulerService` para que ambos
serialicen turnos sobre el mismo `(agent_id, channel, chat_id)`. Resuelve un
race pre-existente latente entre user message + scheduled trigger que el
proyecto reconocía con `extra_sections_snapshot` pero no mitigaba a nivel del
historial.

**IMPORTANTE para mantenedores del scheduler**: el adapter
`LLMDispatcherAdapter` se construye **una sola vez** en `AppContainer.__init__`
y se almacena en `self._llm_dispatcher`. El `SchedulerService` (vía
`SchedulerDispatchPorts.llm_dispatcher`) y el `BackgroundDelegationQueueAdapter`
(vía su param `dispatcher`) reciben **la misma instancia** — por eso comparten
el dict interno de locks-por-scope. Si en el futuro alguien refactoriza esto
construyendo instancias separadas, el lock-per-scope deja de serializar entre
los dos paths y vuelve a aparecer el race condition que mitigamos.

**Breaking para callers que asumían sync**: tests que construyen tool_calls
de `delegate` deben pasar `wait=true` explícitamente para preservar el path
legacy. Tests existentes ya actualizados en `tests/unit/use_cases/test_delegation_integration.py`.

### `per-user-context-files`

> **Superseded por `channel-contextid`**: la resolución por `{username}.md` →
> `{user_id}.md` que describe esta nota se reemplazó por una clave única
> `context_id` (`chat_id or user_id`), idéntica en privado y grupo — el `username`
> ya NO nombra el archivo. Se conserva como registro histórico.

El archivo global `~/.inaki/USER.md` se reemplaza por archivos per-user scopeados
por canal. `RunAgentUseCase._read_user_context` ahora resuelve contra el
`ChannelContext` del turno:

```
~/.inaki/users/{channel_type}/{username}.md   ← preferente
~/.inaki/users/{channel_type}/{user_id}.md    ← fallback
(nada)                                        ← si ninguno existe
```

**Razón**: el bot va pisando dirección multiusuario (Telegram con varios
remitentes humanos), y un único `USER.md` global mezclaba contexto. Ahora cada
`(channel, identidad)` carga su propio archivo.

**Sin auto-detección de legacy**. El soporte a `~/.inaki/USER.md` se borra sin
warning ni fallback — coherente con "no sobreingeniar" para uso doméstico.
Migración manual del operador:

```bash
mv ~/.inaki/USER.md ~/.inaki/users/telegram/{tu_username}.md
# Para chat por CLI/REST opcionalmente:
cp ~/.inaki/users/telegram/{tu_username}.md ~/.inaki/users/cli/{tu_user}.md
```

**Auto-creación de subdirs por canal**: el daemon, al arrancar, ejecuta
`ensure_user_channel_dirs(home, registry.list_all())` y crea
`~/.inaki/users/{channel}/` por cada canal configurado en cualquier agente.
Idempotente, errores de OS loguean WARNING sin abortar arranque. Se invoca
también en cada reload (`bootstrap_fn` del daemon) para captar canales nuevos.
Sin sentido detectar "canal con humanos" vs "canal interno" — el costo es
nulo y simplifica.

**Wiring CLI/REST**: el admin chat router (`/admin/chat/turn`) lee
`channels.cli.user` del YAML del agente y lo inyecta como `username` en el
`ChannelContext`. Sin esa entrada, el lookup cae al fallback por `user_id`
(`session_id` del cliente) que normalmente no tiene archivo → sin contexto.

**Telegram ya estaba listo**: el bot pobla `username` y `user_id` en el
`ChannelContext` desde `update.message.from_user` (privados).

> **Superseded (grupos)**: este párrafo originalmente decía "en grupos no se
> carga contexto per-user". Eso fue cierto solo hasta `081144b`/`#34`, el MISMO
> día, ~1h después: ese commit agregó el heurístico de último-emisor para
> resolver `{{CHANNEL.SENDER}}` y reutilizó el mismo `ChannelContext` que ya
> alimentaba `_read_user_context` — sin actualizar esta nota. Ver
> `group-context-by-chat-id` para el comportamiento real/actual.

**Defensa contra path traversal**: si `username` o `user_id` contienen `/`, `\`
o `..`, ese candidato se descarta. Paranoia barata — los valores vienen del
canal, pero no costaba nada chequear.

### `turn-kill-switch`

Comando `/stop` de Telegram: aborta **mecánicamente** el turno en curso del
chat. Escribir "para" depende de que el LLM lea el mensaje drenado y obedezca
(compliance probabilística); `/stop` es el pedal de freno: marca un flag de
cancelación en el `IScopeRegistry` (`request_cancel(scope)` — solo si el scope
está busy; `mark_idle` lo limpia SIEMPRE para que un /stop tardío no envenene
el próximo turno) y `run_tool_loop` lo chequea en el checkpoint A y **antes de
cada tool del batch**. Al detectar la cancelación: las tools restantes del
batch reciben un resultado sintético (`_CANCELLED_TOOL_RESULT` — preserva el
pairing protocolar assistant↔results, sin esto el provider tira 400), el loop
corta, y una última llamada SIN tools con la instrucción `_CANCEL_WRAPUP_INSTRUCTION`
(user sintético, solo en working_messages, jamás persistido) produce el resumen
de dónde quedó el trabajo. Si esa llamada falla, cierre fijo ("🛑 Tarea
detenida..."). Wiring: `RunAgentUseCase` recibe `scope_registry` del container
y lo pasa al loop; los one-shot NO lo reciben (sin kill-switch en subagentes).
El comando es admin-only (`_is_allowed`) como el resto de los slash commands.

**Sin migración de DB ni cambios de config.**

### `incremental-persist`

La persistencia del rastro del turno (narración + tool calls) ahora es **en
caliente, mensaje a mensaje**, para los turnos que NO pueden terminar en
`__SKIP__` — y la decisión se toma AL INICIO del turno: `incremental = not
ephemeral and skip_marker is None`. Un turno conversacional (Telegram privado,
CLI/REST) jamás skipea → cada assistant+tool_calls, tool result y bloque de
narración se persiste al generarse. Un turno skip-capaz (grupos autonomous,
scheduler `agent_send`, bg-results) conserva el batch post-loop legacy porque
su persistencia depende del desenlace (`__SKIP__` = no persistir nada — la
semántica original de skip queda INTACTA donde cumple su función: evitar que
los turnos-ruido autónomos contaminen el historial).

Qué gana: (1) un crash/restart del daemon a mitad de un research de minutos ya
no evapora el rastro completo (caso real: restart-loop de anacleto vía
update.sh); (2) `history.db` es observable en vivo durante el turno; (3) los
mensajes in-flight del usuario quedan intercalados en su orden real de llegada.

**Componentes**: `run_tool_loop` acepta `persist_message` (callback por mensaje
del trace — assistant+tool_calls, results, circuit-open, cancelados); con flag
`persist_tool_calls` OFF la narración se persiste vía `PersistingIntermediateSink`
(`_turn_pipeline.py`, hermano incremental de `RecordingIntermediateSink`). El
mapeo decisión→mecanismo vive en `RunAgentUseCase._execute_turn`; el guard
post-loop `not ephemeral and not skip_persist` queda SOLO para los turnos batch.

**Normalización de grupos protocolares** (`_drop_orphan_tool_messages`,
reescrita): además de los tool results huérfanos (ventana/trim), ahora cubre
los dos casos que la persistencia incremental vuelve cotidianos — (a) **grupo
incompleto** (assistant+tool_calls cuyos results faltan: crash a mitad de
batch) → se descarta el grupo entero; (b) **users intercalados dentro de un
grupo** (in-flight injection persistió mientras las tools corrían) → se
reubican inmediatamente después del grupo completo (coincide con lo que el LLM
vio en vivo y respeta la contigüidad que exigen los providers). El matching es
por `tool_call_id` (antes solo posicional). NUNCA persistir un grupo confiando
en que "el load lo arregla" sin esta normalización.

**Sin migración de DB ni cambios de config.** Filas idénticas a las del batch,
solo cambia CUÁNDO se escriben.

### `in-flight-message-injection`

Mensajes nuevos del usuario sobre un scope `(agent_id, channel, chat_id)` que ya
tiene un `execute()` corriendo ahora se persisten en `history.db` vía
`record_user_message` y el tool loop del turno en curso los drena entre
iteraciones (checkpoints A: antes de `llm.complete`; B: después del batch
completo de `tool_calls`). El LLM ve los mensajes drenados como `role=user`
en `working_messages` en la siguiente llamada y decide la semántica — enriquecer,
corregir, o abortar la tarea. No hay señales especiales: una sección del system
prompt (`_INFLIGHT_CLARIFICATIONS_SECTION` en `run_agent.py`, en inglés) le
explica al LLM cómo interpretarlos.

Cuando el drain devuelve mensajes no-vacíos, el contador `tool_call_max_iterations`
resetea a 0 — sin esto, un enriquecimiento en iter 4/5 dejaría solo 1 iteración
para incorporar el cambio. El `circuit_breaker` NO se resetea (fallos reales de
tools siguen acumulando).

**Componentes nuevos**:
- `core/ports/outbound/scope_registry_port.py` — `IScopeRegistry` con
  `try_mark_busy(scope) -> bool` y `mark_idle(scope) -> None`. Type alias
  `Scope = tuple[str, str, str]`.
- `adapters/outbound/scope_registry_adapter.py` — `InMemoryScopeRegistryAdapter`
  con `set` protegido por un `asyncio.Lock` global. Una sola instancia compartida
  entre todos los agentes (los scopes ya están aislados por `agent_id`).
- `_tool_loop.py` recibe params opcionales `history_store` y `scope`; con
  `None` el comportamiento es legacy (backward-compat 100%).

**FIX drainage-por-cursor (2026-07-12)**: el drain original CONTABA los mensajes
`role=user` sobre `load()` — que aplica la ventana `chat_history.max_messages`.
Con la ventana LLENA (toda conversación madura), cada mensaje nuevo expulsa una
fila vieja del borde: si la expulsada era `user`, el conteo no crece y el drain
quedaba CIEGO — el "para" del usuario jamás llegaba al LLM (bug real cazado con
journalctl vacío de `[in-flight]`: no era el modelo desobedeciendo, el mensaje
nunca se inyectó). También rompía con `merge_chats` (baseline sin scope vs drain
scoped) y necesitaba el workaround `initial_db_user_count` para el coalesce.
Reemplazo: **cursor por rowid monotónico** — `IHistoryStore.last_row_id` +
`load_user_messages_since(after_id)` (filas `role=user` con id > cursor, query
por índice, sin ventana). El baseline es el id que devuelve el `append` del
user_msg (o `MAX(id)` del scope en los flujos history-derived/ephemeral); el
loop lo bootstrapea solo si el caller no lo pasa. `initial_db_user_count` se
eliminó — el coalesce ya no necesita workaround (el cursor no cuenta nada).
NUNCA volver a un drain por conteo sobre una vista ventaneada.

**Routing en inbound adapters** (`bot.py:_run_pipeline`, `chat.py:chat_turn`,
`agents.py:chat`):
```
if try_mark_busy(scope):
    try: execute() finally: mark_idle(scope)
else:
    record_user_message(message, channel, chat_id)
    return ACK "📝 incorporando a la tarea en curso..."
```

**Sin migración de DB ni cambios de config**. El feature es 100% in-memory: si
el daemon reinicia con scopes marcados busy, todos vuelven a estar libres
(mismo trade-off que `background-delegation` — uso doméstico Pi 5).

**Behavior shift observable**: dos mensajes seguidos del usuario sobre el mismo
scope ahora producen **UNA respuesta combinada** en vez de dos turnos secuenciales.
Antes M2 esperaba a que M1 terminara y disparaba un turno nuevo desde cero
(perdiendo el trabajo previo). Ahora M2 se incorpora al loop en curso.

**Grupos de Telegram EXCLUIDOS**. `_run_group_pipeline` mantiene el flow legacy
con `_schedule_group_flush` + buffer-delay-coalesce + `_extract_trailing_user_batch`.
Razón: durante el delay random NO hay `execute()` corriendo, así que la
"injection in-flight" no aplica. El delay ES su ventana de coalescencia natural.
En `_run_pipeline` el branch in-flight se activa solo cuando `not es_grupo and
user_input is not None` (también skip cuando `user_input=None` para no romper
el path history-derived de fotos enriquecidas).

**FIX checkpoint C (2026-08-01)**: los checkpoints A y B dejaban un agujero en
la SALIDA del loop. Cuando el LLM devolvía una respuesta sin `tool_calls`,
`run_tool_loop` hacía `return` **sin drenar**: todo lo que el usuario mandó
mientras se generaba ESA respuesta quedaba huérfano — nadie lo drenaba y
`mark_idle` no re-chequea nada, así que el mensaje dormía en `history.db` hasta
el próximo turno. Pero el usuario YA había recibido el ACK *"Lo incorporo a lo
que estoy haciendo"*. **El ACK mentía.**

Esta nota describía ese agujero como *"race window narrow — microsegundos entre
`mark_idle` y `try_mark_busy`"*. **Era falso**: la ventana real era toda la
generación de la respuesta final (segundos con un provider remoto en la Pi).

**Checkpoint C** drena justo antes del `return`. Si aparece algo, la respuesta
deja de ser final y pasa a ser un **borrador de contexto**: queda en
`working_messages` (el LLM no pierde el trabajo redactado), el loop reentra, y
el usuario recibe UNA respuesta que ya contempla las dos cosas. Recién ahora la
ventana residual es la que este párrafo declaraba: los pocos ms entre el
checkpoint y `mark_idle`.

El borrador **NO se emite al sink y NO se persiste**: el usuario nunca lo vio.
Misma regla que `__SKIP__` — *no entregado ⇒ no persistido*. Y no contradice
`intermediate-persist`: esa nota persiste la narración PORQUE el usuario la vio.

El bookkeeping de resets es el mismo de A y B (extraído a
`_account_inflight_drain` — con tres checkpoints, tres copias era garantía de
divergencia). **De esa contabilidad depende que el loop termine**: pasado
`_MAX_INFLIGHT_ITER_RESETS` el contador avanza, o un usuario que escribe en cada
respuesta mantiene el turno vivo para siempre.

**Grupos**: el párrafo de arriba dice que están excluidos, y es cierto para el
*routing* inbound (no hay `try_mark_busy`; el buffer-delay hace de coalescencia).
Pero el drain SÍ corre en el flush de grupo — `_execute_turn` siempre pasa
`history_store` + `scope` al loop —, así que el checkpoint C también cierra ahí
la fuga equivalente: un mensaje que llegaba mientras `_run_group_pipeline`
generaba su respuesta no lo veía nadie, y `_schedule_group_flush` no crea flush
nuevo mientras el task corre (ni hay ACK que delate la pérdida).

**Re-routing in-flight de tools (2026-08-01)**: el semantic routing corre UNA
vez por turno, con la query del PRIMER mensaje. Un in-flight que cambia de tema
no traía sus tools (el page-in solo salva si el LLM ADIVINA el nombre de una que
no ve). Ahora, tras cada drain no vacío, el loop llama el callback opcional
`reroute_tools` y **UNE** el resultado al set visible. Nunca reemplaza: el turno
tiene trabajo en vuelo con las tools viejas. La política vive en
`RunAgentUseCase` (bypass por input corto, routing inactivo, respeto a
`tools_override`); el loop solo une, acotado por `reroute_max_extra_tools`
(= `tools_top_k`) porque un set visible sin techo degrada la selección del LLM.
Un fallo del re-routing se loguea y el turno sigue.

**Alcance deliberado: SOLO tools.** Skills y knowledge chunks NO se recalculan —
viven en el system prompt, que se arma una vez por turno; rehacerlos obligaría a
reconstruirlo entre iteraciones e invalidaría el prompt caching del provider. Es
otra feature, no un olvido.

**Costo I/O**: cada iteración del tool loop hace 2 queries adicionales a SQLite
(checkpoints A y B), más una en la salida (checkpoint C). Con el drainage por
cursor son deltas por índice (`WHERE id > ?`), aún más baratas que el load
completo original — overhead despreciable en la Pi 5.

### `telegram-group-auth`

La matriz de autorización del canal Telegram se separó por contexto. El guardián
único es `TelegramBot._is_authorized(update)`, que compone los dos building blocks
existentes según el tipo de chat:

- **Privado**: filtra por `allowed_user_ids` (lista vacía = todos). Sin cambios.
- **Grupo**: filtra SOLO por `allowed_chat_ids`. `allowed_user_ids` ya **no aplica**
  en grupos — cualquier miembro de un grupo autorizado puede interactuar.
- **`allowed_chat_ids` vacío**: el bot **NO responde en grupos** (solo privados).

**Breaking change de comportamiento** (sin migración de DB ni de config): antes,
`allowed_chat_ids: []` significaba "todos los grupos aceptados" — el código
contradecía su propio docstring. Ahora vacío = ningún grupo. Configs que tenían
`allowed_chat_ids: []` y dependían de responder en grupos **dejan de hacerlo**.

**Paso del operador**: para seguir respondiendo en un grupo, agregar su `chat_id`
a `channels.telegram.allowed_chat_ids` (obtenible con `/chatid` dentro del grupo).

La matriz aplica uniforme a los 4 handlers de mensaje (texto, foto, voz, media
silenciosa). Los **comandos slash** (`/start`, `/clear`, `/scheduler`, etc.) quedan
fuera: siguen siendo admin-only por `allowed_user_ids` vía `_is_allowed`, incluso
en grupos autorizados (`/chatid` mantiene su bypass de `allowed_chat_ids`).

### `group-context-by-chat-id`

> **Superseded por `channel-contextid`**: el campo `is_group` y la dicotomía
> `is_group ? chat_id : username→user_id` de esta nota se eliminaron. Todos los
> canales (privado y grupo) resuelven ahora por `context_id` (`chat_id or user_id`).
> Se conserva como registro histórico.

`RunAgentUseCase._read_user_context` resolvía el archivo de contexto en chats
grupales por **el último emisor humano que escribió antes del flush**
(heurística "last sender wins" de `group_flow.py`, pensada originalmente solo
para resolver `{{CHANNEL.SENDER}}`, ver nota de doc drift en
`per-user-context-files`). Eso era sorpresivo: el contexto inyectado podía
cambiar de turno a turno según quién hubiera hablado último, y el fallback sin
`@username` caía a `ctx.user_id` — que en el path de grupos es el **id del
agente** (`TelegramBotSettings.id`), no un id de usuario real, así que en la
práctica casi nunca resolvía nada.

**Cambio**: un grupo ahora se trata como su propia entidad, identificada por su
`chat_id` (estable), igual que ya hacían el digest de memoria y `agent_state`
(ver `memory-scoped-by-channel-chat` / `agent-state-scoped-by-channel-chat`).
`ChannelContext` suma el campo `is_group: bool = False`
(`core/domain/value_objects/channel_context.py`). `_read_user_context` resuelve:

```
~/.inaki/users/{channel_type}/_common.md      ← siempre, sin cambios
# is_group=True (grupo):
~/.inaki/users/{channel_type}/{chat_id}.md    ← único candidato, SIN fallback
# is_group=False (resto):
~/.inaki/users/{channel_type}/{username}.md   ← preferente
~/.inaki/users/{channel_type}/{user_id}.md    ← fallback
```

Sin fallback a `username`/`user_id` en grupos a propósito: esos campos siguen
poblados (heurística de último emisor) pero ahora SOLO alimentan
`{{CHANNEL.SENDER}}` y afines — no la identidad del grupo. El único lugar que
construye `ChannelContext` para turnos grupales es `group_flow.py:213`
(`_run_group_pipeline`); ahí se setea `is_group=True`. `bot.py:407`
(`_run_pipeline`) es privado-only en la práctica — todo mensaje de grupo (texto,
foto, voz, álbum) pasa por el buffer de `group_flow.py`.

**Sin migración de DB ni cambios de config obligatorios.** `is_group` tiene
default `False` — call sites existentes (CLI/REST, privados) no cambian de
comportamiento. **Paso opcional del operador**: para darle contexto propio a un
grupo, crear `~/.inaki/users/telegram/{chat_id}.md` (chat_id negativo en
Telegram, obtenible con `/chatid` dentro del grupo — ver `telegram-group-auth`).
Sin ese archivo, un grupo solo recibe `_common.md` (si existe).

### `intermediate-persist`

> **Actualizado por `incremental-persist` (2026-07-12)**: el mecanismo de
> acumular-y-persistir-al-final que describe esta nota quedó SOLO para los
> turnos skip-capaces (skip_marker seteado). Los turnos conversacionales
> persisten la narración EN CALIENTE vía `PersistingIntermediateSink`.

Los textos que el LLM emite junto con `tool_calls` durante el tool loop (narración
en vivo — "ok, dejame revisar esto...") se entregaban al canal (Telegram en vivo,
REST/CLI, scheduler) vía `IIntermediateSink.emit()` pero **jamás quedaban en
history.db**: `_execute_turn` solo persistía la respuesta final del turno (la
última llamada del LLM sin `tool_calls`). El usuario veía varios mensajes en el
chat; el agente, en el próximo turno, solo tenía el último en su propio contexto
— podía creer que no había hecho el trabajo que ya había narrado y repetir el
loop completo. Reportado por el usuario 2026-07-01.

Es la imagen especular de `background-delegation` (Defecto 2, aceptado como
diseño): ahí el resultado de una delegación bg-N se persiste pero no se
entrega crudo al usuario. Acá era al revés — se entregaba pero no se
persistía — y a diferencia de ese caso, esto sí era un defecto: nadie decidió
deliberadamente que la narración en vivo debiera perderse.

**Fix**: `RunAgentUseCase._execute_turn` envuelve el `intermediate_sink` recibido
con `RecordingIntermediateSink` (`core/use_cases/_turn_pipeline.py`) antes de
pasarlo a `run_tool_loop`. El wrapper reenvía cada `emit()` al sink real (la
entrega en vivo no cambia) y además lo acumula en `.messages`, en orden. Tras el
loop, bajo el mismo guard `not ephemeral and not skip_persist` que ya gateaba la
respuesta final, `_execute_turn` persiste cada intermedio acumulado como
`Message(role=ASSISTANT)` — en orden, ANTES de la respuesta final.

Cubre los tres canales con un solo cambio: el turno conversacional en vivo
(`TelegramLiveIntermediateSink`), REST/CLI (`BufferingIntermediateSink`,
`adapters/inbound/rest/admin/routers/chat.py`) y los turnos que dispara el
scheduler — `agent_send`, resultados de delegación bg-N — porque
`LLMDispatcherAdapter.dispatch` invoca el mismo `agent.run_agent.execute(...)`
(`adapters/outbound/scheduler/dispatch_adapters.py:150`), no un one-shot.
`RunAgentOneShotUseCase` (subagentes de delegación) queda afuera a propósito:
no pasa `history_store`/`scope` a `run_tool_loop`, no tiene historial propio
que resolver.

**Sin migración de DB ni cambios de config.** No hay cambio de schema — son más
filas `Role.ASSISTANT` de las que ya se escriben hoy. `_tool_loop.py` y
`ToolLoopMaxIterationsError` quedan intactos: como el wrapper acumula en vivo
durante el loop, `.messages` está completo tanto si el turno termina normal
como si corta por `ToolLoopMaxIterationsError` — no hizo falta tocar el
contrato de `run_tool_loop` ni propagar nada a través de la excepción.

### `channel-contextid`

`RunAgentUseCase._read_user_context` resolvía el archivo de contexto per-entidad
con una dicotomía: en grupos por `chat_id` (vía `is_group`), en el resto por
`{username}.md`→`{user_id}.md` (ver `per-user-context-files` y
`group-context-by-chat-id`, ambas superseded). El problema: ese archivo es una
**memoria caliente que el LLM edita a voluntad** — el operador le indica el path en
el system prompt con una variable `{{CHANNEL.*}}`. Y NINGUNA variable existente
coincidía con la clave de LECTURA en todos los contextos: con `{{CHANNEL.USERNAME}}`
divergía en grupos (usaba el último emisor), con `{{CHANNEL.CHATID}}` divergía en
privado (leía por `username`→`user_id`). El LLM escribía en un path y el agente
leía de otro.

**Cambio**: una clave canónica única. `ChannelContext.context_id`
(`@computed_field` = `chat_id or user_id`) es la fuente ÚNICA para la LECTURA
(`_read_user_context` → `candidates = (ctx.context_id,)`) y la ESCRITURA (el LLM,
vía la variable de prompt nueva `{{CHANNEL.CONTEXTID}}`). Misma resolución en
privado y grupo:

```
~/.inaki/users/{channel_type}/_common.md       ← siempre, sin cambios
~/.inaki/users/{channel_type}/{context_id}.md  ← único candidato (context_id = chat_id or user_id)
```

Se **eliminó el campo `is_group`** (quedó huérfano al sacar su único lector). El
`username` sigue poblado pero SOLO alimenta `{{CHANNEL.USERNAME}}`, no el nombre del
archivo. `context_id` nunca es vacío (el validador garantiza `user_id`), así que la
variable siempre resuelve salvo en turnos sin `ChannelContext` (scheduler).

**BREAKING de DATOS (sin auto-migración)**: los archivos privados nombrados por
`@username` (`alberto.md`) dejan de leerse. Paso del operador: `mv
~/.inaki/users/telegram/alberto.md ~/.inaki/users/telegram/<chat_id>.md` y cambiar
la variable del system prompt a `{{CHANNEL.CONTEXTID}}`. En CLI/REST, `context_id`
deriva de `channels.cli.user` (identidad estable) cuando el cliente no manda
`chat_id`; sin ese config, del `session_id` efímero (sin archivo pre-escribible) —
por eso el router usa `user_id = cli_user or session_id`. Sin cambios de DB ni de
config. Ver `docs/configuracion.md` → "Per-entity context files" y
`docs/prompt_builder.md` (tabla de variables).
