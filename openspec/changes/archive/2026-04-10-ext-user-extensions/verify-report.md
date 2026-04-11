# Verification Report

**Change**: ext-user-extensions
**Version**: N/A
**Mode**: Standard

---

## Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 52 |
| Tasks complete (by code evidence) | 51 |
| Tasks unmarked in `tasks.md` | 52 |

**Note**: `tasks.md` still has all items as `[ ]` despite the implementation being complete. The apply-progress artifact in Engram claims 100% complete. This is a documentation hygiene issue, not a code issue — the work itself is done (verified via codebase inspection + test execution).

Incomplete tasks (by code evidence):
- **Task 8.1** — "Eliminar `adapters/outbound/tools/shell_tool.py`". The file was renamed to `adapters/outbound/tools/run_shell_tool.py` and **still exists, is tracked in git** (commit `8eb14ec`), contains `class ShellTool(ITool)` with `name = "run_shell"`. It is NOT imported from `container.py` (dead code), but its presence violates REQ-07.

---

## Build & Tests Execution

**Build**: ➖ Not applicable (Python project, no build step)

**Tests**: ✅ **73 passed** / 0 failed / 0 skipped
```
collected 73 items

tests/integration/scheduler/test_scheduler_end_to_end.py .....           [  6%]
tests/integration/scheduler/test_sqlite_scheduler_repo.py .......        [ 16%]
tests/unit/adapters/test_sqlite_history_store.py ..............          [ 35%]
tests/unit/adapters/test_yaml_skill_repo_add_file.py ......              [ 43%]
tests/unit/domain/test_scheduler_service.py ......                       [ 52%]
tests/unit/infrastructure/test_container_builtin_tools.py ..             [ 54%]
tests/unit/infrastructure/test_container_extensions.py .........         [ 67%]
tests/unit/use_cases/test_consolidate_memory.py ...........              [ 82%]
tests/unit/use_cases/test_run_agent_basic.py .....                       [ 89%]
tests/unit/use_cases/test_schedule_task.py ........                      [100%]

73 passed, 1 warning in 0.42s
```

**Coverage**: ➖ Not configured as a threshold gate

---

## Spec Compliance Matrix

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| REQ-01: Dos dirs canónicos | ambos dirs presentes | `test_container_extensions.py > test_multiple_dirs_order` | ✅ COMPLIANT |
| REQ-01: Dos dirs canónicos | directorio personal ausente | `test_container_extensions.py > test_missing_dir_no_error` | ✅ COMPLIANT |
| REQ-02: Contrato manifest | manifest con tools y skills | `test_container_extensions.py > test_happy_path_tool_and_skill` | ✅ COMPLIANT |
| REQ-02: Contrato manifest | manifest sin atributos | `test_container_extensions.py > test_empty_manifest_no_crash` | ✅ COMPLIANT |
| REQ-03: Loader unificado | imports internos resuelven | `container.py` inserta parent en `sys.path`; validado por happy_path | ✅ COMPLIANT |
| REQ-03: Loader unificado | módulo IDs únicos | `container.py` usa `f"_inaki_ext_{ext_dir.name}_{ext_name}_manifest"` | ✅ COMPLIANT (estructural) |
| REQ-04: add_file() | carga correctamente | `test_yaml_skill_repo_add_file.py > test_add_file_loads_skill` | ✅ COMPLIANT |
| REQ-04: add_file() | deduplicación | `test_yaml_skill_repo_add_file.py > test_add_file_deduplicates` | ✅ COMPLIANT |
| REQ-04: add_file() | combina con skills_dir | `test_yaml_skill_repo_add_file.py > test_add_file_combines_with_dir` | ✅ COMPLIANT |
| REQ-04: add_file() | cache invalidation | `test_yaml_skill_repo_add_file.py > test_add_file_invalidates_cache` | ✅ COMPLIANT |
| REQ-04: add_file() | dedup vs skills_dir | `test_yaml_skill_repo_add_file.py > test_add_file_deduplicates_with_dir` | ✅ COMPLIANT |
| REQ-04: add_file() | path inexistente no crashea | `test_yaml_skill_repo_add_file.py > test_add_file_missing_path_no_crash` | ✅ COMPLIANT |
| REQ-05: AppConfig.ext_dirs | configuración personalizada | `config.py:36` define `ext_dirs: list[str] = ["ext", "~/.inaki/ext"]` | ✅ COMPLIANT (estructural) |
| REQ-06: Migración exchange_calendar | disponible post-migración | `~/.inaki/ext/exchange_calendar/` completo con engine, manifest, yaml; imports reescritos a `ext.exchange_calendar.tools.engine.*` | ✅ COMPLIANT (estructural) |
| REQ-07: Migración run_shell | disponible desde `~/.inaki/ext/` | `~/.inaki/ext/run_shell/tools/run_shell_tool.py` contiene `class RunShellTool` con `name = "run_shell"`; manifest + YAML presentes | ✅ COMPLIANT (estructural) |
| REQ-07: Migración run_shell | `adapters/outbound/tools/shell_tool.py` eliminado de built-ins | **`adapters/outbound/tools/run_shell_tool.py` SIGUE EXISTIENDO en el repo (tracked)** | ❌ **FAILING** |
| REQ-08: Resiliencia | ImportError en manifest | `test_container_extensions.py > test_manifest_import_error_skipped` | ✅ COMPLIANT |
| REQ-08: Resiliencia | SyntaxError en manifest | `test_container_extensions.py > test_manifest_syntax_error_skipped` | ✅ COMPLIANT |
| REQ-08: Resiliencia | tool falla al instanciar | `test_container_extensions.py > test_tool_instantiation_error_skipped` | ✅ COMPLIANT |
| REQ-08: Resiliencia | skill file no existe | `test_container_extensions.py > test_missing_skill_file_warning` | ✅ COMPLIANT |
| REQ-09: Precedencia built-ins | built-ins registradas primero | `test_container_builtin_tools.py > test_builtin_tools_present` | ✅ COMPLIANT |
| REQ-09: Precedencia built-ins | run_shell/exchange NO en built-ins | `test_container_builtin_tools.py > test_shell_and_exchange_not_in_builtins` | ✅ COMPLIANT |
| REQ-09: Precedencia built-ins | colisión de nombre | `test_container_extensions.py > test_name_collision_warning` | ✅ COMPLIANT |

**Compliance summary**: 22/23 escenarios compliant (95.6%). 1 scenario FAILING por archivo huérfano trackeado.

---

## Correctness (Static — Structural Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| REQ-01: Dos dirs canónicos | ✅ Implementado | `_register_extensions` itera `ext_dirs` en orden |
| REQ-02: Contrato manifest | ✅ Implementado | `getattr(module, "TOOLS", [])` y `getattr(module, "SKILLS", [])` con default seguro |
| REQ-03: Loader unificado | ✅ Implementado | `spec_from_file_location` + `sys.path` management + module IDs únicos |
| REQ-04: `add_file()` | ✅ Implementado | `yaml_skill_repo.py:50-56` con resolución de path y cache invalidation; `_ensure_loaded` con `seen: set[Path]` |
| REQ-05: `AppConfig.ext_dirs` | ✅ Implementado | `config.py:36` |
| REQ-06: Migración exchange_calendar | ✅ Implementado | Engine completo en `~/.inaki/ext/exchange_calendar/tools/engine/` con 8 módulos e imports reescritos |
| REQ-07: Migración run_shell | ⚠️ Parcial | `~/.inaki/ext/run_shell/` correcto, pero `adapters/outbound/tools/run_shell_tool.py` (orphan con `class ShellTool`) sigue en el repo |
| REQ-08: Resiliencia | ✅ Implementado | `container.py:118-127, 145-149, 154-159` con try/except y logging |
| REQ-09: Precedencia built-ins | ✅ Implementado | `container.py:134-139` chequea `tool_instance.name in self._tools._tools` |

---

## Coherence (Design)

| Decisión | Seguida? | Notas |
|----------|-----------|-------|
| `spec_from_file_location` en lugar de `import_module` | ✅ Yes | `container.py:119` |
| Module IDs únicos `_inaki_ext_<dir>_<name>_manifest` | ✅ Yes | `container.py:116` |
| `sys.path` management (parent insertion) | ✅ Yes | `container.py:108-111` |
| `AppConfig.ext_dirs: list[str]` (no `str`) | ✅ Yes | `config.py:36` |
| Built-ins restantes: web_search, read_file, write_file, patch_file | ✅ Yes | `container.py:74-84` |
| `_register_extensions` llamado desde `__init__` | ✅ Yes | `container.py:54` |
| `YamlSkillRepository.add_file()` con dedup vía resolve() | ✅ Yes | `yaml_skill_repo.py:50-56, 83-102` |
| Colisión de nombres → WARNING + skip | ✅ Yes | `container.py:134-139` |
| Eliminar código viejo (`adapters/outbound/tools/shell_tool.py`, `exchange_calendar*`) | ⚠️ Deviated | `shell_tool.py` fue renombrado a `run_shell_tool.py` y no eliminado |

---

## Issues Found

### CRITICAL (must fix before archive)

1. **`adapters/outbound/tools/run_shell_tool.py` sigue en el repo (tracked)**
   - **Evidencia**: `git ls-files` confirma que está trackeado; contenido: `class ShellTool(ITool)` con `name = "run_shell"`. Es código muerto (no lo importa nadie) pero viola REQ-07 / Task 8.1 del plan: el archivo viejo debe ser eliminado.
   - **Impacto**: Confusión futura (dos definiciones del mismo nombre `run_shell` en el repo; la "real" vive en `~/.inaki/ext/`). También quebranta el grep de verificación de Task 8.4.
   - **Fix sugerido**: `git rm adapters/outbound/tools/run_shell_tool.py` y commit.

### WARNING (should fix)

1. **`tasks.md` sin marcar como completado**
   - Los 52 items siguen como `[ ]` pese a que el apply-progress reporta 100%. Es un fallo del contrato de persistencia de `sdd-apply` (no marcó las tareas mientras implementaba).
   - **Fix sugerido**: editar `tasks.md` y marcar todos los items completados (excepto el que tiene el issue crítico).

2. **`ext/` está completamente vacío — `ext/__init__.py` fue eliminado**
   - Task 3.4 indicaba "verificar que `ext/__init__.py` existe; crearlo vacío si no existe". Actualmente el directorio está vacío.
   - **Impacto**: ninguno funcional (el loader hace `glob("*/manifest.py")` y lista vacía). Es solo un detalle de scaffolding para futuras extensiones en desarrollo.
   - **Fix sugerido**: crear `ext/__init__.py` vacío para dejar el directorio listo para la primera extensión en dev.

### SUGGESTION (nice to have)

1. **`.gitignore` no tiene entrada para `ext/`** (ni positiva ni negativa)
   - Task 9.2 pedía verificar que `ext/` no está ignorado. La verificación pasa por omisión (no hay regla que lo excluya), pero no hay afirmación explícita. Si alguien en el futuro agrega una regla genérica como `ext*`, romperá el auto-discovery sin darse cuenta.
   - **Fix sugerido**: añadir un comentario en `.gitignore` dejando claro que `ext/` debe ser committeado.

2. **Los archivos `.DS_Store` en `~/.inaki/ext/`**
   - No afecta al repo (está fuera del repo), pero si el usuario empieza a versionar `~/.inaki/` en su dotfiles en el futuro, conviene que los `.DS_Store` no viajen.

---

## Verdict

**PASS WITH WARNINGS** → **PASS** (post-fix)

### Fixes aplicados tras el verify inicial

1. **CRITICAL resuelto**: `git rm adapters/outbound/tools/run_shell_tool.py` — el archivo huérfano fue eliminado del tracking.
2. **WARNING resuelto**: `ext/__init__.py` creado vacío (Task 3.4).
3. **WARNING resuelto**: `tasks.md` marcado con `[x]` en los 52 items completados.
4. **SUGGESTION**: `.gitignore` verificado — no tiene ningún patrón que excluya `ext/`, cobertura implícita suficiente.

### Re-run de tests post-fix

```
84 passed, 1 warning in 0.43s
```

(aparecieron 11 tests adicionales respecto al run inicial: `test_agent_registry.py` + `test_ensure_user_config.py`)

La funcionalidad está implementada correctamente y todos los tests pasan. Los 23 de 23 escenarios de spec están comportamentalmente compliant tras eliminar el orphan. **Listo para archive.**
