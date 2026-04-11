# Tasks: ext-user-extensions

## Phase 1: YamlSkillRepository — add_file()

Archivo: `adapters/outbound/skills/yaml_skill_repo.py`

- [x] 1.1 Añadir `_extra_files: list[Path] = []` al `__init__`
- [x] 1.2 Extraer método privado `_load_skill_from_path(self, yaml_file: Path) -> None`
- [x] 1.3 Refactorizar `_ensure_loaded()` para usar `_load_skill_from_path` y un `seen: set[Path]` con `path.resolve()` al procesar `skills_dir`
- [x] 1.4 Extender `_ensure_loaded()` para procesar `_extra_files` (en orden, deduplicando contra `seen`)
- [x] 1.5 Implementar `add_file(self, path: Path) -> None`: resolver path, skip si ya está en `_extra_files`, append, `_loaded = False`

**Tests** — `tests/unit/adapters/test_yaml_skill_repo_add_file.py`

- [x] 1.6 Test: `add_file` carga skill correctamente (presente en `list_all()`)
- [x] 1.7 Test: `add_file` + `skills_dir` combinados — skills de ambas fuentes
- [x] 1.8 Test: `add_file` invalida cache (`list_all()` → `add_file()` → `list_all()` = N+1)
- [x] 1.9 Test: deduplicado — misma path dos veces → una sola entrada
- [x] 1.10 Test: deduplicación vs `skills_dir` — extra ya bajo `skills_dir` → no duplicada
- [x] 1.11 Test: path inexistente → warning, no lanza excepción

## Phase 2: AppConfig — ext_dirs

Archivo: `infrastructure/config.py`

- [x] 2.1 Reemplazar `ext_dir: str = "ext"` por `ext_dirs: list[str] = ["ext", "~/.inaki/ext"]` en `AppConfig`
- [x] 2.2 Verificar que el YAML merge (`_deep_merge`) maneja listas correctamente (si `global.yaml` define `ext_dirs`, la lista se sobreescribe completa, no se hace append — es el comportamiento correcto)

## Phase 3: Limpieza de ./ext/

- [x] 3.1 Eliminar `ext/run_shell/` completo (se moverá a `~/.inaki/ext/` en Phase 5)
- [x] 3.2 Eliminar `ext/web_search/` completo (scaffolding huérfano sin manifest válido)
- [x] 3.3 Eliminar `ext/exchange_calendar/skill.yaml` (archivo vacío con nombre incorrecto)
- [x] 3.4 Verificar que `ext/__init__.py` existe; crearlo vacío si no existe
- [x] 3.5 Vaciar `ext/exchange_calendar/manifest.py` (actualmente vacío/inválido — se reescribirá en Phase 6)

## Phase 4: Crear estructura ~/.inaki/ext/

- [x] 4.1 Crear `~/.inaki/ext/__init__.py` (vacío)
- [x] 4.2 Crear `~/.inaki/ext/exchange_calendar/__init__.py` (vacío)
- [x] 4.3 Crear `~/.inaki/ext/exchange_calendar/tools/__init__.py` (vacío)
- [x] 4.4 Crear `~/.inaki/ext/exchange_calendar/tools/engine/__init__.py` (vacío)
- [x] 4.5 Crear `~/.inaki/ext/run_shell/__init__.py` (vacío)
- [x] 4.6 Crear `~/.inaki/ext/run_shell/tools/__init__.py` (vacío)

## Phase 5: Migrar exchange_calendar a ~/.inaki/ext/

El código viejo en `adapters/` se mantiene intacto hasta Phase 8.

**Engine** — copiar `adapters/outbound/tools/exchange_calendar/*.py` → `~/.inaki/ext/exchange_calendar/tools/engine/*.py` reescribiendo imports:

- [x] 5.1 Copiar `time_utils.py` → `engine/time_utils.py` (sin imports cruzados del paquete viejo — verificar)
- [x] 5.2 Copiar `notifications.py` → `engine/notifications.py` (verificar imports)
- [x] 5.3 Copiar `tracing.py` → `engine/tracing.py` (verificar imports)
- [x] 5.4 Copiar `config_store.py` → `engine/config_store.py` (verificar imports)
- [x] 5.5 Copiar `calendar_env.py` → `engine/calendar_env.py`; reescribir `from adapters.outbound.tools.exchange_calendar.time_utils` → `from ext.exchange_calendar.tools.engine.time_utils`
- [x] 5.6 Copiar `reader.py` → `engine/reader.py`; reescribir imports a `ext.exchange_calendar.tools.engine.*`
- [x] 5.7 Copiar `writer.py` → `engine/writer.py`; reescribir imports a `ext.exchange_calendar.tools.engine.*`
- [x] 5.8 Copiar `engine.py` → `engine/engine.py`; reescribir todos los imports `from adapters.outbound.tools.exchange_calendar.X` → `from ext.exchange_calendar.tools.engine.X`

**Facade** — copiar `adapters/outbound/tools/exchange_calendar_tool.py`:

- [x] 5.9 Copiar → `~/.inaki/ext/exchange_calendar/tools/exchange_calendar_tool.py`; reescribir:
  - `from adapters.outbound.tools.exchange_calendar.engine import` → `from ext.exchange_calendar.tools.engine.engine import`
  - `from adapters.outbound.tools.exchange_calendar.calendar_env import` → `from ext.exchange_calendar.tools.engine.calendar_env import`

**Verificación**:

- [x] 5.10 Smoke test: `PYTHONPATH=~/.inaki python -c "from ext.exchange_calendar.tools.exchange_calendar_tool import ExchangeCalendarTool; print('OK')"` → imprime OK

**Manifest + skill YAML**:

- [x] 5.11 Crear `~/.inaki/ext/exchange_calendar/manifest.py`:
  ```python
  from ext.exchange_calendar.tools.exchange_calendar_tool import ExchangeCalendarTool
  TOOLS = [ExchangeCalendarTool]
  SKILLS = ["exchange_calendar.yaml"]
  ```
- [x] 5.12 Crear `~/.inaki/ext/exchange_calendar/exchange_calendar.yaml` con id, name, description, instructions y tags (ver design.md sección 7)

## Phase 6: Migrar run_shell a ~/.inaki/ext/

- [x] 6.1 Copiar `ext/run_shell/tools/run_shell_tool.py` → `~/.inaki/ext/run_shell/tools/run_shell_tool.py` (sin cambios de imports — ya implementa ITool directamente)
- [x] 6.2 Crear `~/.inaki/ext/run_shell/manifest.py`:
  ```python
  from ext.run_shell.tools.run_shell_tool import RunShellTool
  TOOLS = [RunShellTool]
  SKILLS = ["run_shell.yaml"]
  ```
- [x] 6.3 Crear `~/.inaki/ext/run_shell/run_shell.yaml` con id, name, description, instructions y tags (ver design.md sección 7)
- [x] 6.4 Smoke test: `PYTHONPATH=~/.inaki python -c "from ext.run_shell.tools.run_shell_tool import RunShellTool; print(RunShellTool.name)"` → imprime `run_shell`

## Phase 7: Switch atómico en container

Archivo: `infrastructure/container.py`

- [x] 7.1 Añadir método `_register_extensions(self, ext_dirs: list[str]) -> None` con la implementación exacta del design (spec_from_file_location, glob, TOOLS/SKILLS, colisión de nombres, warning+skip en excepciones)
- [x] 7.2 En `_register_tools()`: eliminar import y registro de `ShellTool` (`adapters.outbound.tools.shell_tool`)
- [x] 7.3 En `_register_tools()`: eliminar import y registro de `ExchangeCalendarTool` (`adapters.outbound.tools.exchange_calendar_tool`)
- [x] 7.4 Llamar `self._register_extensions(global_config.app.ext_dirs)` al final de `AgentContainer.__init__()`, después de `self._register_tools()`

**Tests** — `tests/unit/infrastructure/__init__.py` + `tests/unit/infrastructure/test_container_extensions.py`:

- [x] 7.5 Crear `tests/unit/infrastructure/__init__.py` vacío
- [x] 7.6 Test: directorio inexistente → no error, no-op
- [x] 7.7 Test: happy path — manifest con `TOOLS` + `SKILLS` → tool registrada, `add_file` llamado
- [x] 7.8 Test: manifest con `SyntaxError` → WARNING, resto de extensiones procesan
- [x] 7.9 Test: manifest con `ImportError` → WARNING, resto procesa
- [x] 7.10 Test: manifest sin `TOOLS`/`SKILLS` → no crash
- [x] 7.11 Test: `ToolClass()` tira → WARNING, no aborta
- [x] 7.12 Test: skill YAML declarada no existe → WARNING, no añade
- [x] 7.13 Test: colisión de nombre con built-in → WARNING, tool original intacta
- [x] 7.14 Test: múltiples dirs → orden correcto (proyecto antes que personal)

**Regression guard** — `tests/unit/infrastructure/test_container_builtin_tools.py`:

- [x] 7.15 Test: built-ins presentes — `web_search`, `read_file`, `write_file`, `patch_file` registradas (con `ext_dirs=[]`)
- [x] 7.16 Test: `run_shell` y `exchange_calendar` NO están en built-ins cuando `ext_dirs=[]`

## Phase 8: Eliminar código viejo

Solo ejecutar DESPUÉS de que Phase 7 esté verde.

- [x] 8.1 Eliminar `adapters/outbound/tools/shell_tool.py` (eliminado también el orphan `run_shell_tool.py` post-verify)
- [x] 8.2 Eliminar `adapters/outbound/tools/exchange_calendar_tool.py`
- [x] 8.3 Eliminar `adapters/outbound/tools/exchange_calendar/` completo
- [x] 8.4 Grep de verificación: `grep -rn "adapters.outbound.tools.exchange_calendar\|shell_tool\|ShellTool" . --include="*.py"` → cero matches en código (excluir `__pycache__`)

## Phase 9: Verificación final

- [x] 9.1 Ejecutar suite completa — sin regresiones (73/73)
- [x] 9.2 Verificar `.gitignore`: `ext/` NO ignorado; `~/.inaki/` no aplica (fuera del repo)
- [x] 9.3 Smoke test manual: arrancar el agente, confirmar en logs:
  - `"Extensión 'exchange_calendar': tool 'exchange_calendar' registrada"`
  - `"Extensión 'run_shell': tool 'run_shell' registrada"`
- [x] 9.4 Verificar que el agente responde a prompts que activan `exchange_calendar` y `run_shell`
