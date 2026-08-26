# Plan de refactorización — Sistema de configuración

> Estado: EN PROGRESO — Fases 1 a 6 implementadas. Documento vivo.
> Origen: auditoría del 2026-08-25 (Engram `architecture/config-system-audit` y
> `architecture/config-secrets-layer`).
> Última actualización: 2026-08-25.
> Rama de trabajo: `refactor/config-secrets-eradication`.

## Diagnóstico (resumen de la auditoría)

El merge por capas NO es el problema — es un patrón correcto y `_deep_merge` son 12
líneas. Los problemas reales, en orden de gravedad:

1. **Cinco motores de merge/herencia conviviendo** con semánticas distintas:
   `_deep_merge` (loader), `SENTINEL_ELIMINAR` + tri-estado (`core/use_cases/config/_merge.py`,
   carril de edición), `merged_llm_config` (field-by-field con `model_fields_set`),
   `inherit: true` (`resolve_inherit`), y `build_ephemeral_child` (`container.py:846`,
   5ª capa en runtime para sub-agentes, invisible a toda UI).
2. **`AgentConfig.channels: dict[str, dict[str, Any]]`** — 26 campos (14% del schema)
   jamás validados al cargar; `bot.py:103-160` triplica defaults con `.get()`; typos
   silenciosos; el generador de docs no puede descender; el TUI necesitó un registry
   inyectado a mano.
3. **Cultura de fallo silencioso**: agente roto → `None` + WARNING (desaparece);
   `extra="ignore"` casi en todo; `_coerce_timeout` traga basura.
4. **Un dato viaja por 7-8 representaciones**, con renombrados gratuitos en el camino
   (`digest_filename` → `digest_template`, `semantic_routing_top_k` → `tools_top_k`).
5. **Cuatro fuentes de docs desincronizadas**: `configuracion.md` (1665 líneas),
   `config-reference.md` (64% descripciones vacías, faltan las 6 clases Telegram),
   `global.example.yaml` (faltan 5 bloques), docstrings del schema.
6. **`*.secrets.yaml` sin razón de ser**: son YAML plano (el cifrado Fernet vive solo
   en `tool_config.yaml`); su único valor era compartir config sin credenciales, caso
   que no se usa ni se va a usar. Decisión: erradicarlos (4 capas → 2). La secret-ness
   se conserva como METADATO del schema (`kind == "secret"`), no como split de ficheros.

Puntos fuertes que NO se tocan: merge por capas como concepto, round-trip ruamel con
escritura atómica, schema Pydantic introspectable con docstrings, migraciones one-shot
con orden "escribir antes de borrar", disciplina hexagonal con ratchet.

**Fuera de scope explícito** (no sobreingeniar): librerías externas de config
(OmegaConf/Hydra/Dynaconf — no resuelven estos problemas), hot-reload del daemon,
merge de listas por id, y el rediseño del setup TUI (plan propio en
`docs/setup-tui-redesign-plan.md`; la Fase 5 de este plan es su prerequisito).

## Reglas de ejecución

- Cada fase es **shippeable por separado**: gate = `ruff check` + `mypy` + `pytest`
  verdes antes de pasar a la siguiente.
- Todo breaking change o migración se documenta en `docs/migraciones.md` (convención
  del repo) y actualiza `CLAUDE.md` si toca una regla que figura ahí ("merge de 4
  capas" aparece varias veces).
- Migraciones one-shot: idempotentes, escribir lo nuevo ANTES de borrar lo viejo
  (duplicado benigno > pérdida de datos).
- Rama y mensajes de commit: pedir nombre/aprobación al usuario (git workflow del repo).

---

## Fase 1 — Erradicar `*.secrets.yaml` (4 capas → 2) ✅ HECHA

**Objetivo**: el modelo mental pasa a "2 ficheros por contexto": `config/global.yaml`
y `agents/{id}.yaml`. La marca de secreto queda en el schema, no en el filesystem.

1. Migración one-shot en bootstrap (patrón `migrate_tool_config_to_own_file`):
   - Mergear `global.secrets.yaml` → `global.yaml` y cada `agents/{id}.secrets.yaml` →
     `agents/{id}.yaml` (deep merge, el secrets pisa).
   - `chmod 600` al fichero principal resultante.
   - Borrar el secrets SOLO tras escribir y verificar el principal.
   - Idempotente: si no hay secrets, no-op.
2. Loader: eliminar las lecturas/merges de secrets (`config_loader.py:524, 705`).
3. Bootstrap: eliminar la creación de `global.secrets.yaml` con header
   (`config_loader.py:238-245`).
4. `LayerName`: 6 → 3 valores (fuera `GLOBAL_SECRETS`, `AGENT_SECRETS`,
   `SUB_AGENT_SECRETS`) en `core/ports/config_repository.py` y todo su fan-out.
5. TUI: borrar `screens/secrets_page.py` (191 líneas), la entrada SECRETS del menú, y
   el routing por `kind == "secret"` en el carril de escritura. **Conservar** el
   masking visual de campos secret.
6. `yaml_repository.py`: chmod 600 pasa a aplicarse a los ficheros principales
   (contienen credenciales ahora).
7. Docs: `configuracion.md`, `CLAUDE.md` (sección Configuration), `migraciones.md`
   (nota nueva con la acción del operador: ninguna, migración automática).

**Costo asumido y aceptado**: `cat global.yaml` ya no es pegable sin filtrar llaves.
Lo cubre el `config show` con redacción (Fase 5).

## Fase 2 — Validar `channels` al cargar (matar el dict laxo) ✅ HECHA

**Objetivo**: los 26 campos de Telegram/Broadcast se validan en el arranque, con la
misma fuente de verdad que ya usa el TUI. Un typo en `channels.telegram` FALLA.

1. Registry de schemas de canal: `CHANNEL_SCHEMAS`. Quedó en `config_schema.py` y no
   en un módulo propio para no crear un ciclo de imports (indexa clases definidas ahí).
   Es la única fuente; `setup_cli.py` la importa en vez de armar el dict a mano.
2. Validación en el `field_validator` de `AgentConfig.channels`, NO en el loader: así
   es la única puerta y cubre los cuatro caminos que construyen un `AgentConfig`
   (loader, builder efímero, admin, tests). El bloque se coerciona a su modelo;
   properties `telegram` / `cli` para acceso tipado. Se tipó `cli` por primera vez.
3. `adapters/inbound/telegram/bot.py:103-160`: recibe el modelo validado; borrar los
   ~30 `.get()` con defaults triplicados y los `hasattr(x, "model_dump")` defensivos.
4. `container.py:1872-1886` (`_wire_broadcast_for_agent`): la validación ya ocurrió en
   el loader → simplificar; un fallo acá deja de ser alcanzable en silencio.
5. `config_docs.py`: descender por el registry → las 6 clases Telegram/Broadcast
   aparecen en `config-reference.md`.
6. TUI `_schema_tree.py`: el `if name == "channels"` hardcodeado pasa a apoyarse en el
   registry compartido (inyectado igual que hoy, pero desde la fuente única).
7. Revisar si `_filter_channel_adapters` (contaminación global↔agente por la colisión
   de nombre `channels`) sigue siendo necesario; si sí, dejar comentario con el porqué.

## Fase 3 — Motor de merge ÚNICO ✅ HECHA

**Objetivo**: una sola semántica de merge/herencia/borrado, usada por el loader, el
carril de edición del TUI y los sub-agentes efímeros.

1. Módulo nuevo `core/domain/config_merge.py` (puro, sin terceros — cumple el
   allowlist de `core/`): `deep_merge`, sentinel de borrado, resolución de
   `inherit`, y **tracking opcional de procedencia** (qué capa aportó cada clave —
   insumo directo de la Fase 5).
2. Tabla de semántica documentada EN el módulo (fuente única):
   - dict ⊕ dict → merge recursivo
   - lista → reemplazo total (nunca concat; documentar el footgun de
     `knowledge.sources`)
   - `null` explícito → pisa
   - ausente → hereda
   - sentinel → borra la clave
   - scalar vs dict entre capas → **error ruidoso** (hoy: reemplazo silencioso)
   - `inherit: true` → merge del bloque del padre bajo el del hijo, opt-in por bloque
3. Migrar consumidores, uno por commit:
   - `config_loader.py::_deep_merge` y `resolve_inherit`
   - `core/use_cases/config/_merge.py` (el tri-estado del TUI pasa a expresarse con
     los primitivos del motor: ausente/null/sentinel)
   - `container.py::build_ephemeral_child` (la 5ª capa runtime usa el mismo motor —
     deja de ser un mundo aparte)
4. `merged_llm_config`: se CONSERVA como excepción única y declarada (opera sobre
   modelos ya validados, no sobre dicts crudos; absorberlo rompería el tri-estado que
   el TUI edita sobre `memories.llm.*`). Documentado en su docstring. Texto original:
   evaluar si el patrón ausente-vs-null de `MemoryLLMConfig` se
   puede expresar con los primitivos del motor ANTES de validar. Si el costo es alto,
   se conserva pero documentado como excepción única, con referencia al motor.
5. La fachada `infrastructure/config.py` reexporta desde el módulo nuevo (sin romper
   el contrato histórico todavía; su muerte es Fase 7).

## Fase 4 — Fallos ruidosos ✅ HECHA

**Objetivo**: aplicar el invariante del repo ("un arranque que no puede fallar es un
arranque que no se puede diagnosticar") al subsistema de config.

1. `load_agent_config` roto → `ConfigError` que ABORTA el arranque, con path del
   fichero y campo culpable (hoy: WARNING y el agente desaparece del registry).
2. `extra="forbid"` NO gradual: puesto en la clase base `_ConfigBaseModel`, cubre los
   37 modelos de una. El colchón (clave desconocida + sugerencia vía `difflib`) es un
   validador de la misma base, así que aplica a todo el schema por igual.
3. Matar la sanitización que traga basura: `_coerce_timeout` / `_coerce_request_delay`
   (`config_schema.py:164-180`) → `ConfigError` (un `timeout_seconds: "sesenta"` debe
   fallar, no correr con 60).
4. Barrido de los 13 `except Exception → logger → continuar` del wiring: 5 pasan a
   fatales (container del agente, delegación, scheduler, broadcast, tools de Telegram);
   los de visión y extensiones quedan degradados a propósito, nombrando la capacidad
   que se pierde; los de shutdown y `adapter.start()` no se tocan.

## Fase 5 — `inaki config show --effective --origin` ✅ HECHA

**Objetivo**: la config efectiva mergeada, anotando de qué capa salió cada valor
(estilo `git config --show-origin`), con secretos redactados. Es LA base para
cualquier interfaz futura: una UI sobre config-efectiva-con-origen es un problema
fácil; sobre N ficheros crudos + semántica de merge, el imposible que el TUI lleva
5.000 líneas peleando.

1. Use case `core/use_cases/config/show_effective.py` apoyado en la procedencia del
   motor (Fase 3): árbol efectivo + origen por clave
   (`global.yaml` / `agents/{id}.yaml` / `SUBAGENT_DEFAULTS` / default del schema).
2. Redacción de campos `kind == "secret"` → `********` (acá rinde el metadato que la
   Fase 1 conservó). **Deuda abierta por la Fase 1**: al borrar la `SecretsPage` se
   perdió la vista transversal "qué credenciales están configuradas y cuáles faltan"
   (`iter_declared_secrets`). Un dump que enmascara responde lo mismo — incluir en el
   output la marca de secreto-declarado-pero-vacío para recuperar esa capacidad.
3. CLI `inaki config show [--agent ID] [--origin] [--json]` como sub-CLI en `inaki/`
   (composition root — los entry points nuevos NO van bajo `adapters/inbound/`).
4. Cubrir también los sub-agentes efímeros (la ex-5ª capa, ya unificada): poder ver
   qué config efectiva le queda a un subagente delegado.
5. Actualizar `docs/setup-tui-redesign-plan.md`: el árbol del TUI pasa a alimentarse
   de este use case en vez de reconstruir el merge por su cuenta.

## Fase 6 — Documentación: una fuente de verdad ✅ HECHA

1. Autogenerar `config/global.example.yaml` desde el schema (extender
   `config_docs.py`, mismo truco que `config-reference.md`) + test de drift. Mueren
   los 5 bloques faltantes y el `_DELEGATION_SECTION_COMMENT` pegado a mano.
2. Completar `config-reference.md`: las clases de canal ya entran (Fase 2); rellenar
   el 64% de descripciones vacías escribiendo los docstrings que faltan en el schema
   (esto es lo laborioso — puede repartirse por subsistema).
3. `configuracion.md`: deja de duplicar la referencia campo a campo; queda la prosa
   (conceptos, ejemplos, casos). Borrar o IMPLEMENTAR la tabla "Field merge rules"
   que el código no impone (p. ej. `memories.db_filename` "solo en global"): si la
   regla vale, el loader la valida (Fase 4); si no, se borra de la doc.
4. Resolver contradicciones docs↔schema detectadas (`system_prompt` "required" en la
   doc vs default `""` en el schema).

## Fase 7 — Limpieza menor

- Renombrados gratuitos en Settings VOs: alinear nombres con el schema
  (`digest_template` → `digest_filename`, `tools_top_k` →
  `tools_semantic_routing_top_k`, etc.) o documentar por qué difieren.
- Campos muertos — la auditoría inicial se equivocó con
  `knowledge.token_budget_warn_threshold`: **está vivo** (`container.py:523` →
  `KnowledgeOrchestrator.token_budget_threshold` → `warn_if_token_budget_exceeded`).
  Los realmente inertes, hallados al documentar el schema (Fase 6):
  `app.name` (cero lecturas en runtime; el nombre que llega al prompt es
  `AgentConfig.name`) y `photos.faces.provider` (`Literal` de un solo valor que
  nadie lee — el adapter se construye directo). Decidir por cada uno: borrar o
  cablear. Ambos quedaron documentados como declarativos, así que no mienten.
- `ChannelsGlobalConfig` (un solo campo, existe para colisionar de nombre): evaluar
  renombrar el bloque global a algo que no choque con `AgentConfig.channels`
  (breaking menor, migración one-shot).
- Deduplicar `adapters/outbound/config_repository/paths.py` vs
  `infrastructure/home.py` (hoy sincronizados por comentario): un Protocol/inyección
  desde el composition root, respetando la regla hexagonal.
- Matar la fachada `infrastructure/config.py` (35 líneas, reexporta 16 símbolos
  privados) migrando sus imports.

---

## Orden y dependencias

```
Fase 1 (secrets)          — independiente, achica todo lo posterior
Fase 2 (channels)         — independiente; habilita partes de 4 y 6
Fase 3 (motor único)      — se apoya en 1 (menos capas que unificar)
Fase 4 (fallos ruidosos)  — se apoya en 2 (channels ya validados)
Fase 5 (config show)      — requiere 3 (procedencia) y 1 (kind=secret para redactar)
Fase 6 (docs)             — requiere 2 (docs de canales); el resto en paralelo
Fase 7 (limpieza)         — al final, o intercalada si un commit la toca de paso
```

Tamaño estimado: Fases 1-2 chicas (días), Fase 3 la más delicada (el motor toca los
tres carriles — ir consumidor por consumidor), Fases 4-7 mecánicas y troceables.

## Checklist de progreso

- [x] Fase 1 — erradicar `*.secrets.yaml` — nota `secrets-layer-eradication` en `docs/migraciones.md`
- [x] Fase 2 — validar `channels` al cargar — nota `channels-validados-al-cargar` en `docs/migraciones.md`
- [x] Fase 3 — motor de merge único — nota `motor-de-merge-unico` en `docs/migraciones.md`
- [x] Fase 4 — fallos ruidosos — nota `config-falla-ruidoso` en `docs/migraciones.md`
- [x] Fase 5 — `inaki config show` — nota `config-show-effective` en `docs/migraciones.md`
- [x] Fase 6 — docs fuente única — nota `docs-de-config-autogeneradas` en `docs/migraciones.md`
- [ ] Fase 7 — limpieza menor
