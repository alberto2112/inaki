# Spec: ext-user-extensions

## Overview

Define los requisitos para separar extensiones de usuario (tools + skills) de los built-ins del sistema mediante dos directorios canónicos con auto-discovery basado en manifest. Elimina la necesidad de modificar `container.py` o `adapters/` al agregar nuevas integraciones.

---

## Requirements

### REQ-01: Dos directorios canónicos de extensiones

El sistema DEBE soportar dos directorios de extensiones, escaneados en orden:

1. `./ext/` — extensiones del proyecto (en el repo, sin secrets)
2. `~/.inaki/ext/` — extensiones personales (fuera del repo, pueden contener credenciales)

Ambos son opcionales: si un directorio no existe, el sistema arranca normalmente y emite DEBUG log.

#### Scenario: ambos dirs presentes

- GIVEN `./ext/foo/manifest.py` y `~/.inaki/ext/bar/manifest.py` existen y son válidos
- WHEN `AppContainer` inicializa
- THEN `foo` y `bar` son descubiertos y registrados; `foo` se registra antes que `bar`

#### Scenario: directorio personal ausente

- GIVEN `~/.inaki/ext/` no existe
- WHEN `AppContainer` inicializa
- THEN el sistema arranca normalmente con las extensiones del proyecto (si las hay)

---

### REQ-02: Contrato del manifest

Cada extensión DEBE proveer `<ext_dir>/<name>/manifest.py` que exponga:

- `TOOLS: list[type[ITool]]` — clases (no instancias); default `[]`
- `SKILLS: list[str]` — paths a YAMLs relativos al manifest; default `[]`

Ambos atributos son opcionales (se usa `getattr(module, "TOOLS", [])`). Una extensión puede declarar solo tools, solo skills, o ambos.

#### Scenario: manifest con tools y skills

- GIVEN `manifest.py` define `TOOLS = [ExchangeCalendarTool]` y `SKILLS = ["exchange_calendar.yaml"]`
- WHEN el auto-discovery lo procesa
- THEN `ExchangeCalendarTool` es instanciada y registrada; `exchange_calendar.yaml` es cargado en `YamlSkillRepository`

#### Scenario: manifest sin atributos

- GIVEN `manifest.py` no define `TOOLS` ni `SKILLS`
- WHEN el auto-discovery lo procesa
- THEN no falla; no registra nada

---

### REQ-03: Loader unificado vía spec_from_file_location

`_register_extensions()` DEBE usar `importlib.util.spec_from_file_location` para cargar manifests por path absoluta. DEBE funcionar tanto para `./ext/` como para `~/.inaki/ext/` sin código diferenciado. DEBE añadir el directorio parent de cada `ext_dir` a `sys.path` (una sola vez) para que los imports internos de los engines resuelvan correctamente.

#### Scenario: imports internos del engine resuelven

- GIVEN `~/.inaki/ext/exchange_calendar/manifest.py` hace `from ext.exchange_calendar.tools.engine.engine import ExchangeCalendarEngine`
- WHEN el manifest es cargado
- THEN el import resuelve correctamente porque `~/.inaki/` está en `sys.path`

#### Scenario: módulo IDs únicos

- GIVEN dos extensiones llamadas `foo` en `./ext/` y `~/.inaki/ext/`
- WHEN ambos manifests son cargados
- THEN no colisionan en `sys.modules` (IDs incluyen el nombre del dir parent)

---

### REQ-04: YamlSkillRepository.add_file()

`YamlSkillRepository` DEBE exponer `add_file(path: Path) -> None` que:
- Añade el path a la lista de archivos extra
- Invalida el cache (`_loaded = False`)
- Deduplica por `path.resolve()` (no carga el mismo archivo dos veces)
- No falla si el path no existe en el momento de la llamada (el error se emite al cargar)

`add_file()` es la ÚNICA fuente de skills del sistema. El repositorio NO escanea ningún directorio base: todo saber de dominio viaja vía extensiones que invocan `add_file()` desde sus `manifest.py`.

#### Scenario: add_file carga skill correctamente

- GIVEN un `YamlSkillRepository` ya cargado con N skills
- WHEN `add_file(path)` es llamado con un YAML válido y luego `list_all()`
- THEN retorna N+1 skills incluyendo la nueva

#### Scenario: deduplicación

- GIVEN `add_file(p)` llamado dos veces con la misma path resuelta
- WHEN `list_all()` es llamado
- THEN la skill aparece una sola vez

#### Scenario: repositorio vacío

- GIVEN un `YamlSkillRepository` sin llamadas a `add_file()`
- WHEN `list_all()` es invocado
- THEN retorna lista vacía sin error

---

### REQ-05: AppConfig.ext_dirs

`AppConfig` DEBE incluir `ext_dirs: list[str] = ["ext", "~/.inaki/ext"]`. Este valor DEBE ser usado por `_register_extensions()` como lista de directorios a escanear. Los paths son expandidos con `Path.expanduser().resolve()` antes de usarse.

#### Scenario: configuración personalizada

- GIVEN `global.yaml` define `app.ext_dirs: ["extensions"]`
- WHEN `AppContainer` inicializa
- THEN solo `extensions/` es escaneado

---

### REQ-06: Migración de exchange_calendar a ~/.inaki/ext/

La extensión `exchange_calendar` DEBE ser reubicada desde `adapters/outbound/tools/exchange_calendar*` a `~/.inaki/ext/exchange_calendar/` con el layout:

```
~/.inaki/ext/exchange_calendar/
  __init__.py
  manifest.py
  exchange_calendar.yaml
  tools/
    __init__.py
    exchange_calendar_tool.py
    engine/
      __init__.py
      engine.py / reader.py / writer.py / calendar_env.py / ...
```

Todos los imports internos DEBEN ser actualizados a `ext.exchange_calendar.tools.engine.*`.

#### Scenario: exchange_calendar disponible post-migración

- GIVEN la extensión está en `~/.inaki/ext/exchange_calendar/` y `_register_extensions()` corre
- WHEN el agente ejecuta la tool `exchange_calendar`
- THEN responde igual que antes de la migración

---

### REQ-07: Migración de run_shell a ~/.inaki/ext/

La tool `run_shell` DEBE ser reubicada desde `./ext/run_shell/` a `~/.inaki/ext/run_shell/` con el layout:

```
~/.inaki/ext/run_shell/
  __init__.py
  manifest.py
  run_shell.yaml
  tools/
    __init__.py
    run_shell_tool.py
```

El archivo `adapters/outbound/tools/shell_tool.py` DEBE ser eliminado de los built-ins (su funcionalidad es cubierta por la extensión).

#### Scenario: run_shell disponible desde ~/.inaki/ext/

- GIVEN `~/.inaki/ext/run_shell/manifest.py` es válido
- WHEN el agente recibe un prompt que activa la tool `run_shell`
- THEN ejecuta el comando correctamente

---

### REQ-08: Resiliencia ante manifest malformado

Si un manifest falla al importar (`ImportError`, `SyntaxError`, etc.) o una clase falla al instanciar, el sistema DEBE:
- Emitir WARNING con el nombre de la extensión y el error
- Saltar esa extensión (o esa tool individual)
- Continuar cargando el resto de extensiones

#### Scenario: ImportError en manifest

- GIVEN `ext/broken/manifest.py` importa una dependencia que no existe
- WHEN `_register_extensions()` lo procesa
- THEN WARNING logueado; otras extensiones cargan normalmente

---

### REQ-09: Precedencia — built-ins primero, proyecto antes que personal

El orden de registro DEBE ser:
1. Built-ins (`_register_tools()`)
2. Extensiones de `./ext/` (proyecto)
3. Extensiones de `~/.inaki/ext/` (personal)

Si una extensión intenta registrar una tool cuyo `name` ya está registrado, DEBE loguear WARNING y NO sobrescribir la tool existente.

#### Scenario: colisión de nombre con built-in

- GIVEN un built-in `web_search` ya está registrado
- WHEN una extensión intenta registrar una tool con `name = "web_search"`
- THEN la extensión es rechazada con WARNING; el built-in permanece activo

---

## Invariants

- `ITool` (`core/ports/outbound/tool_port.py`) NO DEBE cambiar.
- `ISkillRepository` (`core/ports/outbound/skill_port.py`) NO DEBE cambiar.
- `YamlSkillRepository` carga skills EXCLUSIVAMENTE vía `add_file()`: no existe directorio base escaneado, no existen skills built-in declarativas. El core no define saber de dominio.
- Built-ins restantes tras el cambio: `web_search_tool`, `read_file_tool`, `write_file_tool`, `patch_file_tool`.
- El sistema de merge de config en `infrastructure/config.py` NO DEBE ser restructurado.

---

## Out of Scope

- Hot-reload de extensiones en runtime.
- Versionado o resolución de dependencias de extensiones.
- Filtrado de extensiones por agente.
- Packaging de extensiones como paquetes Python.
- Migración de config a `~/.inaki/` (change separado).
- Cambios al subsistema LLM, embedding, memory o scheduler.
