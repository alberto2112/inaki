# Proposal: ext-user-extensions

## Intent

El proyecto actualmente mezcla tools y skills built-in (shell, web_search, read_file, write_file, patch_file) con extensiones específicas del usuario (exchange_calendar, run_shell) en los mismos directorios. Esto genera tres problemas concretos:

1. **Acoplamiento de infraestructura y extensiones**: `container.py::_register_tools()` hardcodea todas las tools juntas. Añadir una integración nueva obliga a modificar el container, violando open/closed.
2. **Sin convención de extensión**: no hay una ubicación canónica ni un contrato para que un usuario agregue su propia integración (tools + skills + código auxiliar) sin tocar el core.
3. **Sin separación entre desarrollo y producción de extensiones**: no existe un ciclo de vida claro para una extensión — desde su creación hasta que está lista para uso estable.

El cambio introduce dos directorios de extensiones con auto-discovery basado en manifest y un ciclo de vida explícito:

- **Develop/test** → `./ext/<name>/` (en el repo, auto-cargado)
- **Producción** → `~/.inaki/ext/<name>/` (fuera del repo, manual — el usuario mueve la extensión cuando está lista)

Agregar una extensión nueva en desarrollo: crear `./ext/<name>/`, declarar `TOOLS` y `SKILLS` en `manifest.py`, y reiniciar. Cero ediciones al core. Cuando está OK para producción: mover manualmente a `~/.inaki/ext/`.

## Scope

**Dentro:**
- Definir dos directorios canónicos de extensiones con ciclo de vida explícito:
  - `./ext/` — desarrollo y testing de extensiones (en el repo, cargadas automáticamente)
  - `~/.inaki/ext/` — extensiones en producción (fuera del repo, el usuario las mueve manualmente cuando están listas)
- Introducir auto-discovery unificado en `container.py` que escanee ambos dirs usando `spec_from_file_location` (no `importlib.import_module` — funciona con paths absolutas, sin dependencia de `sys.path` para el manifest en sí).
- `AppConfig` pasa de `ext_dir: str` a `ext_dirs: list[str]` con ambos dirs como default.
- Mover `exchange_calendar` desde `adapters/outbound/tools/` a `~/.inaki/ext/exchange_calendar/`.
- Mover `run_shell` desde `./ext/run_shell/` a `~/.inaki/ext/run_shell/`, adaptándola al contrato de manifest.
- Eliminar `shell_tool.py` de built-ins (su funcionalidad pasa a ser extensión de usuario vía `run_shell`).
- Refactorizar `YamlSkillRepository` para aceptar archivos de skill individuales (`add_file(path: Path)`).
- Actualizar imports en tests.

**Fuera:**
- Migración de config a `~/.inaki/config.yaml` — change separado.
- Sistema de plugins externos (instalables vía pip / entry points).
- Sandboxing, permisos o aislamiento de extensiones.
- Hot reload de extensiones en runtime.
- Cambios al contrato `ITool` en `core/ports/outbound/tool_port.py`.
- Migrar skills built-in (`skills/web_search.yaml`) a `ext/`.

## Approach

### 1. Dos directorios de extensiones

```
./ext/               ← develop + test (en el repo, auto-cargado)
  <name>/
    manifest.py
    ...          ↓ mover manualmente cuando está lista para producción

~/.inaki/ext/        ← producción (fuera del repo, privado)
  exchange_calendar/
    manifest.py
    exchange_calendar.yaml
    tools/
      exchange_calendar_tool.py
      engine/  (engine.py, reader.py, ...)
  run_shell/
    manifest.py
    run_shell.yaml
    tools/
      run_shell_tool.py
```

### 2. Contrato del manifest (idéntico en ambos dirs)

```python
# ~/.inaki/ext/exchange_calendar/manifest.py
from ext.exchange_calendar.tools.exchange_calendar_tool import ExchangeCalendarTool

TOOLS = [ExchangeCalendarTool]      # clases ITool (no instancias)
SKILLS = ["exchange_calendar.yaml"] # paths relativas al manifest
```

### 3. Loader unificado con spec_from_file_location

```python
def _register_extensions(self, ext_dirs: list[str]) -> None:
    import importlib.util, sys
    from pathlib import Path

    for ext_dir_str in ext_dirs:
        ext_dir = Path(ext_dir_str).expanduser().resolve()
        if not ext_dir.exists():
            logger.debug("ext dir no encontrado: %s", ext_dir)
            continue
        # Añadir parent al sys.path para que los imports internos del manifest resuelvan
        parent = str(ext_dir.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        for manifest_path in sorted(ext_dir.glob("*/manifest.py")):
            ext_name = manifest_path.parent.name
            module_id = f"_inaki_ext_{ext_dir.name}_{ext_name}_manifest"
            try:
                spec = importlib.util.spec_from_file_location(module_id, manifest_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as exc:
                logger.warning("Extensión '%s': falló al cargar (%s) — skipping", ext_name, exc)
                continue
            for ToolClass in getattr(module, "TOOLS", []) or []:
                try:
                    self._tools.register(ToolClass())
                except Exception as exc:
                    logger.warning("Extensión '%s': falló al instanciar %r (%s)", ext_name, ToolClass, exc)
            for skill_rel in getattr(module, "SKILLS", []) or []:
                skill_path = (manifest_path.parent / skill_rel).resolve()
                if skill_path.exists():
                    self._skills.add_file(skill_path)
                else:
                    logger.warning("Extensión '%s': skill no encontrada: %s", ext_name, skill_path)
```

### 4. AppConfig

```python
class AppConfig(BaseModel):
    ...
    ext_dirs: list[str] = ["ext", "~/.inaki/ext"]
```

### 5. YamlSkillRepository.add_file()

```python
def add_file(self, path: Path) -> None:
    path = Path(path)
    if path in self._extra_files:
        return
    self._extra_files.append(path)
    self._loaded = False
```

## Affected Files

**Eliminados del repo:**
- `adapters/outbound/tools/exchange_calendar_tool.py`
- `adapters/outbound/tools/exchange_calendar/` (completo)
- `adapters/outbound/tools/shell_tool.py` (run_shell pasa a extensión de usuario)
- `ext/run_shell/` (se mueve a `~/.inaki/ext/run_shell/`)
- `ext/web_search/` (scaffolding huérfano, eliminar)

**Creados en `~/.inaki/ext/` (fuera del repo):**
- `~/.inaki/ext/exchange_calendar/manifest.py`
- `~/.inaki/ext/exchange_calendar/exchange_calendar.yaml`
- `~/.inaki/ext/exchange_calendar/tools/exchange_calendar_tool.py`
- `~/.inaki/ext/exchange_calendar/tools/engine/*.py`
- `~/.inaki/ext/run_shell/manifest.py`
- `~/.inaki/ext/run_shell/run_shell.yaml`
- `~/.inaki/ext/run_shell/tools/run_shell_tool.py`

**Modificados:**
- `infrastructure/container.py` — `_register_tools()` pierde shell + exchange_calendar; nuevo `_register_extensions(ext_dirs)`
- `infrastructure/config.py` — `AppConfig.ext_dirs: list[str]`
- `adapters/outbound/skills/yaml_skill_repo.py` — `add_file()` + refactor `_ensure_loaded()`
- `tests/unit/**` — actualizar imports afectados

## Tradeoffs

**Ventajas:**
- Separación clara: built-ins (sistema) vs extensiones de proyecto vs extensiones personales
- `~/.inaki/ext/` es privado por naturaleza — credenciales nunca llegan al repo
- `spec_from_file_location` funciona con paths absolutas, sin magia de `sys.path` para el manifest en sí
- Agregar una extensión nueva: crear directorio + manifest, reiniciar. Cero ediciones al core
- Orden determinístico: built-ins → proyecto (`./ext/`) → personal (`~/.inaki/ext/`)

**Desventajas:**
- `~/.inaki/ext/` no está bajo control de versiones — setup manual por máquina
- `sys.path` se modifica en startup (necesario para imports internos del engine de cada extensión)
- `run_shell` deja de ser built-in — si `~/.inaki/ext/` no está configurado, la tool no está disponible

## Risks

1. **`run_shell` ya no es built-in** — si el usuario arranca el agente sin `~/.inaki/ext/run_shell/`, la tool no existe. Mitigación: documentar en README/setup y loguear claramente al startup.
2. **Colisión de nombres entre extensiones** — `ToolRegistry.register()` debe detectar duplicados. Orden: built-ins primero, luego `./ext/`, luego `~/.inaki/ext/`.
3. **`sys.path` global modificado en startup** — side effect controlado; se inserta una sola vez por directorio parent; no afecta imports del core.
4. **Tests que referencian `adapters.outbound.tools.exchange_calendar*` o `shell_tool`** — grep exhaustivo antes del delete; actualizar en commit atómico.
5. **`~/.inaki/ext/` puede no existir** — el loader hace `if not ext_dir.exists(): continue`, arranque siempre limpio.

## Out of Scope

- Migración de config a `~/.inaki/` — change separado
- Plugins externos instalables (pip / entry points)
- Sandboxing o permisos por extensión
- Hot reload de extensiones
- CLI para scaffolding (`inaki ext new foo`)
- Versionado del contrato del manifest
