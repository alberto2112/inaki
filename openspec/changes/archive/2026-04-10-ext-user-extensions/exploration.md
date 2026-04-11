## Exploration: ext-user-extensions

### Current State

Las tools y skills de usuario están mezcladas con el núcleo built-in del agente:

- `adapters/outbound/tools/exchange_calendar_tool.py` — facade de usuario convive con `shell_tool.py`, `web_search_tool.py`, etc.
- `adapters/outbound/tools/exchange_calendar/` — engine completo (engine.py, reader.py, writer.py, calendar_env.py, config_store.py, tracing.py, notifications.py, time_utils.py) dentro del directorio de tools del núcleo
- `infrastructure/container.py` — `_register_tools()` hardcodea `ExchangeCalendarTool` junto con las tools built-in; cualquier tool nueva requiere editar el container
- `infrastructure/config.py` — `AppConfig.skills_dir: str = "skills"` apunta a un único directorio; no hay concepto de extensiones
- `adapters/outbound/skills/yaml_skill_repo.py` — escanea un único `skills_dir` con `rglob("*.yaml")`; no soporta múltiples orígenes
- `skills/` — YAMLs built-in (`web_search.yaml`, `shell.yaml`) en la raíz del proyecto

El contrato `ITool` está correctamente ubicado en `core/ports/outbound/tool_port.py` — el problema es físico/organizacional, no arquitectónico.

### Affected Areas

- `adapters/outbound/tools/exchange_calendar_tool.py` — mover a `ext/exchange_calendar/tools/`
- `adapters/outbound/tools/exchange_calendar/` — mover a `ext/exchange_calendar/tools/engine/`
- `infrastructure/container.py` — reemplazar `_register_tools()` hardcodeado con auto-discovery via `importlib`
- `infrastructure/config.py` — añadir `ext_dir: str = "ext"` a `AppConfig`
- `adapters/outbound/skills/yaml_skill_repo.py` — refactorizar para aceptar lista de archivos explícita además del dir scan
- `tests/unit/` — actualizar imports que referencien las paths movidas
- El usuario ya empezó a mover tools y skills a la nueva estructura

### Approaches

**Opción B — Manifest + auto-discovery (elegida)**

Cada extensión de usuario vive en `ext/{name}/` con un `manifest.py` que declara sus tools y skills. El container escanea `ext/*/manifest.py` dinámicamente con `importlib` — sin hardcoding.

**Layout de extensión:**
```
ext/
  __init__.py
  exchange_calendar/
    __init__.py
    manifest.py                    ← entry point de la extensión
    exchange_calendar.yaml         ← skill YAML en raíz de la extensión
    tools/
      __init__.py
      exchange_calendar_tool.py    ← facade (implementa ITool)
      engine/                      ← engine completo
        engine.py
        reader.py
        writer.py
        calendar_env.py
        config_store.py
        tracing.py
        notifications.py
        time_utils.py
```

**Manifest:**
```python
# ext/exchange_calendar/manifest.py
from ext.exchange_calendar.tools.exchange_calendar_tool import ExchangeCalendarTool

TOOLS = [ExchangeCalendarTool]
SKILLS = ["exchange_calendar.yaml"]  # paths relativas al manifest
```

**Container con auto-discovery:**
```python
# infrastructure/container.py
def _register_tools(self) -> None:
    # Built-ins (hardcodeados, no cambian)
    self._tools.register(ShellTool())
    self._tools.register(WebSearchTool())
    # ...

    # Extensiones de usuario — auto-discovery
    import importlib
    from pathlib import Path
    ext_dir = Path(self.config.ext_dir)
    for manifest_path in ext_dir.glob("*/manifest.py"):
        ext_name = manifest_path.parent.name
        module = importlib.import_module(f"ext.{ext_name}.manifest")
        for tool_cls in module.TOOLS:
            self._tools.register(tool_cls())
```

**Skills loading desde manifests:**
```python
def _register_skills(self) -> None:
    # Skills built-in desde skills_dir
    skill_files = list(Path(self.config.skills_dir).rglob("*.yaml"))

    # Skills de extensiones
    ext_dir = Path(self.config.ext_dir)
    for manifest_path in ext_dir.glob("*/manifest.py"):
        ext_name = manifest_path.parent.name
        module = importlib.import_module(f"ext.{ext_name}.manifest")
        for skill_file in module.SKILLS:
            skill_files.append(manifest_path.parent / skill_file)

    # yaml_skill_repo recibe lista explícita
    return YamlSkillRepository(skill_files=skill_files)
```

### Recommendation

Implementar Opción B (manifest + auto-discovery). El enfoque es limpio, extensible, y mantiene la arquitectura hexagonal intacta: `ITool` permanece en `core/ports`, la ubicación física de las implementaciones no afecta el contrato.

Agregar una extensión nueva se reduce a: crear `ext/{name}/` con su `manifest.py` — el container la descubre automáticamente sin tocar ningún archivo del núcleo.

### Risks

- **Import paths**: al mover `exchange_calendar/`, todos los imports internos del engine deben actualizarse (relativos → absolutos desde `ext.exchange_calendar.tools.engine.*`). Riesgo bajo, mecánico.
- **Tests**: los tests que importan directamente desde `adapters/outbound/tools/exchange_calendar_tool` necesitan actualización de paths. Riesgo bajo, identificable con búsqueda estática.
- **yaml_skill_repo interfaz**: cambiar de `skills_dir: str` a `skill_files: list[Path]` es un breaking change en la interfaz del repo. Requiere actualizar el container y los tests del repo. Alternativa: aceptar ambos parámetros para compatibilidad.
- **Orden de carga**: si dos extensiones registran una tool con el mismo `name`, habrá colisión en el registry. Considerar detección de duplicados en `tool_registry.py`.
- **`ext/` en `.gitignore`**: si `ext/` se excluye accidentalmente del repo (confundido con `venv/`), las extensiones desaparecen silenciosamente. Verificar `.gitignore`.

### Ready for Proposal

Yes
