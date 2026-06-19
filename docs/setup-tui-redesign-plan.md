# Plan de implementación — Rediseño Setup TUI (split-pane + add/remove por schema)

> Estado: EN PROGRESO. Documento vivo para sobrevivir compactación de contexto.
> Última actualización: 2026-06-19.

## Por qué este rediseño (el problema raíz)

La TUI actual (`GlobalPage`, `AgentDetailPage`) **renderiza TODAS las secciones del
schema Pydantic**, estén o no en el YAML, vía `sections_for_model`. Dos límites
fatales para el caso del usuario:

1. **`AgentConfig.channels: dict[str, dict[str, Any]]`** NO es un `BaseModel` →
   `_schema.py::_should_skip` lo descarta (`_SKIP_ORIGINS = (dict, list, set, frozenset)`).
   Por eso **`channels` no aparece en el setup** aunque en producción el usuario lo
   tiene declarado dentro del agente. Es un dict arbitrario para sobrevivir el merge
   de 4 capas sin validación estricta.
2. **`sections_for_model` solo recursa 2 niveles** (PADRE, PADRE.HIJO). `channels →
   telegram → groups` son 3 niveles. No alcanza.

## La lógica que el usuario quiere (sus palabras, no inventar)

- La config del agente/subagente es **la última capa que se mergea** → todo lo no
  especificado recae sobre ese fichero (hereda de capas previas o del default del schema).
- El TUI **solo renderiza lo que está REALMENTE presente en el YAML**. Nada de mostrar
  el schema entero con campos vacíos.
- El usuario es **libre de añadir o eliminar líneas/grupos**.
- Botón **"añadir sección"** → modal con secciones disponibles (del schema). Ej: añadir
  `channels` → modal con canales disponibles (telegram, cli...). Al elegir, se añade.
- Estando sobre `channels.telegram`, pulsar añadir → modal con **todas las
  subconfiguraciones posibles** de ese nivel (token, allowed_user_ids, groups, broadcast...).
- Adición **granular y explícita**: nada se puebla mágicamente. YAML queda LIMPIO.

## El aspecto visual validado (Propuesta A refinada)

Split-pane: **árbol navegable (izq) + panel de detalle editable (der)**.

- **Árbol** (Textual `Tree`): jerarquía completa de lo presente. `×` por nodo al
  hover/foco = eliminar SECCIÓN entera. Nodos ausentes-pero-añadibles se muestran como
  `+ <nombre>` en dim al final de cada nivel.
- **Panel detalle** (al seleccionar una sección): breadcrumb (`channels › telegram ›
  groups`), nombre de sección, botón **"eliminar sección"** (ícono + TEXTO, sin
  ambigüedad), filas de campos presentes (cada una con `×` al hover = eliminar CAMPO),
  y una zona "+ añadir campo".
- **Modal confirmación de borrado**: lista los campos afectados antes de borrar una sección.
- **Modal añadir**: contextual al nodo; lista opciones addables del schema en ese nivel.
- **SIN footer dinámico** ("eliminar campo 'X'") — el usuario lo descartó: ícono +
  modal de confirmación bastan.
- Footer: keybindings estáticos (↑↓ navegar · Enter editar · a añadir · d eliminar · Esc volver).

## Lo que YA EXISTE y se REUSA (no reinventar)

- **Persistencia con borrado**: `UpdateAgentLayerUseCase` / `UpdateGlobalLayerUseCase` →
  `_deep_merge_con_eliminaciones` YA borra claves vía `_SENTINEL_ELIMINAR`.
  `CampoTriestado(TristadoValor.INHERIT)` resuelve a ese sentinel. Reusable para
  "eliminar clave por path".
- **Repo**: `read_layer`, `write_layer`, `delete_layer`, `render_yaml`, `layer_exists`
  (`core/ports/config_repository.py`). ruamel preserva comentarios.
- **Introspección de tipos**: `_schema.py::_infer_kind`, `_unwrap_optional`, `_is_secret`,
  `_is_long`, `_literal_choices`, `_default_as_str`. Reusar — NO duplicar.
- **Modales de edición**: `modals/{scalar,enum,long,secret,bool,tristate}.py` +
  `_dialog.py::dialog_css`. El flujo Enter→modal→callback→persist en `BasePage` se
  mantiene para EDITAR un campo.
- **Tri-estado `memories.llm.*`**: preservar (inherit/override_value/override_null).
- **Cross-ref warnings post-save**: `BasePage::_warn_on_invalid_refs`.
- **Inyección de schemas**: `SetupContainer` recibe `global_schema`/`agent_schema` del
  composition root (`inaki/setup_cli.py`). El setup_tui NO importa `infrastructure`.

## Decisiones tomadas

1. **`channels` se resuelve inyectando `channel_schemas`** en `SetupContainer`
   (`{"telegram": TelegramChannelConfig, "cli": ...}`), poblado por `setup_cli.py`.
   NO se toca el schema (`channels` sigue siendo `dict[str,dict]` para el merge).
   El builder del árbol usa ese registry para conocer los canales y sus campos.
   → Respeta la regla hexagonal (composition root inyecta, adapter no importa infra).
2. **Añadir = crear la clave "vacía apropiada"**: `{}` para secciones (BaseModel),
   default del schema para campos simples. Nada de poblar defaults masivos. YAML limpio.
   Si una sección creada vacía queda inválida (requeridos sin default), el cross-ref
   warning avisa — aceptable para uso doméstico.
3. **Árbol con Textual `Tree[SchemaNode]`** — idiomático, maneja navegación/expand/
   collapse/selección. NO reinventar la rueda de navegación.
4. **Aplica a las 3 páginas**: global, agentes y sub-agentes.

## Arquitectura nueva

```
adapters/inbound/setup_tui/
├── domain/
│   ├── field.py                 (existe)
│   └── schema_node.py           NUEVO — SchemaNode (nodo del árbol)
├── _schema.py                   (existe — reusar helpers de inferencia)
├── _schema_tree.py              NUEVO — build_schema_tree(model, valores, channel_schemas)
├── _cambios.py                  (existe — quizás extender con build_cambios_por_path)
├── screens/
│   ├── _base.py                 (existe — mantener para compat / extraer helpers)
│   ├── _tree_editor.py          NUEVO — TreeEditorPage: split-pane Tree + detalle
│   ├── global_page.py           REESCRIBIR sobre TreeEditorPage
│   └── agent_detail_page.py     REESCRIBIR sobre TreeEditorPage
├── modals/
│   ├── add_node.py              NUEVO — modal "añadir sección/campo" (lista addables)
│   └── confirm_delete.py        NUEVO — modal confirmación borrado
└── di.py                        EXTENDER — channel_schemas en SetupContainer
```

### `SchemaNode` (domain/schema_node.py)

```python
@dataclass
class SchemaNode:
    path: tuple[str, ...]          # ("channels","telegram","groups")
    label: str                     # "groups"
    is_section: bool               # True=contenedor (BaseModel/dict), False=hoja editable
    present: bool                  # ¿está en el YAML actual?
    field: Field | None            # si es hoja: el Field editable (reusa domain/field.py)
    children: list[SchemaNode]     # sub-nodos presentes
    addable: list[AddableOption]   # qué se puede añadir en este nivel y no está

@dataclass
class AddableOption:
    key: str                       # "groups"
    label: str                     # "groups"
    is_section: bool               # crear {} vs default-de-campo
    description: str = ""          # docstring corto del campo (del FieldInfo)
```

### `build_schema_tree` (_schema_tree.py)

Recorre `model` (Pydantic) + `current_values` (dict del YAML mergeado) recursivamente:
- Campo BaseModel anidado → nodo `is_section=True`; recursa con sub-valores.
- Campo simple → nodo hoja `is_section=False`, `present = key in current_values`,
  `field = Field(...)` (reusando `_infer_kind` etc.).
- **Campo `channels` que es `dict[str,dict]`** (detectar por nombre + tipo dict) → caso
  especial: por cada canal presente en `current_values["channels"]`, crear nodo usando
  `channel_schemas[canal]` como modelo; `addable` = canales del registry no presentes.
- `addable` de cada sección = campos/sub-modelos del schema NO presentes en el YAML.
- Solo se agregan a `children` los nodos PRESENTES (regla "solo lo presente").

### Persistencia add/delete

- **Añadir campo**: `update_layer(cambios = {path...: default_value})`.
- **Añadir sección**: `update_layer(cambios = {path...: {}})`.
- **Eliminar campo/sección**: construir `cambios` con `_SENTINEL_ELIMINAR` en el path.
  Exponer helper público `eliminar_en_path(path: tuple) -> dict` que arme el dict anidado
  terminando en `CampoTriestado(INHERIT)` (ya resuelve a sentinel). Reusa todo el merge.
- Capa destino: secrets si el campo es `kind=="secret"`, sino la principal (igual que hoy).

## Fases de implementación

- [x] **FASE 0 — Modelo + builder del árbol (núcleo sin UI)** ✅
  - `domain/schema_node.py` (SchemaNode + AddableOption + iter_sections/breadcrumb_parts/properties)
  - `_schema_tree.py` (build_schema_tree). channel_schemas en di.py + setup_cli.py.
  - Tests: `test_schema_tree.py` (11). Verde: ruff/mypy/pytest.
- [x] **FASE 1 — Persistencia add/delete por path** ✅
  - `core/use_cases/config/_merge.py` (extraído + COMPARTIDO): SENTINEL_ELIMINAR,
    CampoTriestado, TristadoValor, resolver_tristados, deep_merge_con_eliminaciones.
  - `update_agent_layer.py` y `update_global_layer.py` ahora reusan `_merge` →
    **el global TAMBIÉN borra claves** (capacidad nueva; antes solo agente).
    `update_agent_layer` re-exporta CampoTriestado/TristadoValor (compat imports).
  - `_cambios.py`: `cambios_anidados(path, valor)` + `eliminar_en_path(path)`.
  - Tests: `test_cambios_path.py` + nuevos en `test_update_global_layer.py`. 67 verde.
- [x] **FASE 2 — Widget árbol + página base split-pane** ✅
  - `screens/_tree_editor.py` (TreeEditorPage: Textual Tree izq + VerticalScroll der).
    Navegación árbol↔panel, repoblado async, edición vía modales existentes.
    Hooks abstractos: reload_root, persist_field_saved, persist_tristate_saved,
    persist_add, persist_delete, root_label. `reload_and_repaint` tras add/delete.
  - Tests puros: `test_tree_editor_nav.py` (7). Capa Textual = verificación visual
    (el repo evita Pilot, ver nota en test_base_page_helpers.py).
- [ ] **FASE 3 — Modales add/delete**
  - `modals/add_node.py` (lista `section.addable`), `modals/confirm_delete.py`
    (lista campos afectados). Conectar `_open_add_modal`/`_open_delete_modal` en
    TreeEditorPage → push_screen → callback persist_add/persist_delete + reload_and_repaint.
- [x] **FASE 4 — Migrar GlobalPage / AgentDetailPage a TreeEditorPage** ✅
  - `agent_detail_page.py` reescrito sobre TreeEditorPage: channel_schemas del
    container, exclude_keys={providers}, tri-estado paths dotted lowercase
    (`memories.llm.*`), routing a capa secrets, cross-ref warnings (`_warnings.py`).
    Lee main+secrets mergeados. persist_delete solo poda en la capa donde el path
    EXISTE (`_existe_path`) — evita escribir el sentinel en una rama nueva.
  - `global_page.py` reescrito: root_label="global", capas GLOBAL/GLOBAL_SECRETS.
  - `screens/_warnings.py` (NUEVO): warn_on_invalid_refs extraído (compartido).
  - Tests viejos arreglados: test_agent_detail_helpers (→ `_coerce`),
    test_schema_tristate (set uppercase local). **Suite completa: 2553 verde.**
- [~] **FASE 5 — Cobertura de integración (hecha) + limpieza (DIFERIDA)**
  - [x] `test_page_persistence.py` (10): hooks persist_* de ambas páginas
    construyen el `cambios` por path correcto y eligen capa (principal vs secrets).
  - [x] Comentario obsoleto en `di.py` actualizado (build_schema_tree).
  - [ ] **DIFERIDO hasta validación visual** — borrar `sections_for_model` /
    `_fields_for_model` / `_should_skip` de `_schema.py` (sin uso en prod) +
    reescribir guards groups/broadcast-emit con `build_schema_tree` + borrar tests
    del mecanismo plano. Razón del diferimiento: NO quitar el sistema viejo antes de
    confirmar que `build_schema_tree`+TreeEditorPage funcionan en la TUI real (no hay
    Pilot → la capa Textual no tiene test automático). Es secuenciación prudente, no
    deuda aceptada: se ejecuta apenas el usuario valide `inaki setup`.

## VALIDACIÓN VISUAL PENDIENTE (única tarea bloqueante para cerrar)
Probar `inaki setup` → entrar a un agente con `channels.telegram`:
  1. ¿Aparece el árbol con channels › telegram › groups? (antes channels NO salía)
  2. ↑↓ navega secciones; Enter baja al panel; ↑↓ + Enter edita un campo; Esc sube.
  3. `a` sobre una sección → modal de addables (ej. broadcast bajo telegram).
  4. `d` sobre sección/campo → modal de confirmación → se poda del YAML.
Si algo de la capa Textual falla, ajustar `screens/_tree_editor.py` (eventos
`on_tree_node_highlighted`, mount async). La LÓGICA (builder, persistencia, nav)
está cubierta por tests.

## Estado actual (2026-06-19): FASES 0-4 COMPLETAS Y VERDES. FASE 5 parcial (cobertura
## hecha; limpieza de `sections_for_model` diferida a post-validación-visual).
## Suite completa: 2563 passed. mypy + ruff verdes. SIN COMMIT (pendiente aprobación).

## Invariantes a NO romper

- `adapters/` NUNCA importa `infrastructure/` (test_architecture.py). channel_schemas
  se INYECTAN.
- Guardado inmediato per-edit (no hay botón "guardar global").
- `_notify_daemon_restart_needed` tras cada cambio.
- El setup es OFFLINE (di.py: sin LLM/embedding/daemon).
- Codebase en español (variables, docstrings, comentarios, mensajes).
