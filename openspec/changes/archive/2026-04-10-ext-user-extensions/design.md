# Design: ext-user-extensions

## Architecture Overview

```
                      AgentContainer.__init__
                                │
              ┌─────────────────┼───────────────────┐
              ▼                 ▼                   ▼
       YamlSkillRepository   ToolRegistry    (other adapters)
              │                 │
              └────────┬────────┘
                       ▼
              _register_tools()              ← built-ins: web_search, read, write, patch
              _register_extensions(ext_dirs) ← NEW: ./ext/ → ~/.inaki/ext/
                       │
                       │ spec_from_file_location (path absoluta)
                       ▼
            ┌─────────────────────────────────┐
            │  <ext_dir>/<name>/manifest.py   │
            │    TOOLS  = [ToolClass, …]      │──► self._tools.register(ToolClass())
            │    SKILLS = ["skill.yaml", …]   │──► self._skills.add_file(path)
            └─────────────────────────────────┘
```

**Orden de registro**: built-ins → `./ext/` (alfabético) → `~/.inaki/ext/` (alfabético).
**Colisión de nombres**: tool ya registrada → WARNING + skip (no sobrescribir).
**Contrato**: `ITool` y `ISkillRepository` inmutables.

## Component Changes

### 1. Layout de extensiones

**`./ext/`** (proyecto, en repo — puede estar vacío):
```
ext/
  __init__.py
  <nombre_proyecto>/
    __init__.py
    manifest.py
    <skill>.yaml
    tools/
      __init__.py
      <name>_tool.py
```

**`~/.inaki/ext/`** (personal, fuera del repo):
```
~/.inaki/ext/
  __init__.py
  exchange_calendar/
    __init__.py
    manifest.py
    exchange_calendar.yaml
    tools/
      __init__.py
      exchange_calendar_tool.py
      engine/
        __init__.py
        engine.py / reader.py / writer.py / calendar_env.py
        config_store.py / tracing.py / notifications.py / time_utils.py
  run_shell/
    __init__.py
    manifest.py
    run_shell.yaml
    tools/
      __init__.py
      run_shell_tool.py
```

### 2. Manifest contract

```python
# Ejemplo: ~/.inaki/ext/exchange_calendar/manifest.py
"""Manifest de la extensión exchange_calendar."""
from __future__ import annotations
from ext.exchange_calendar.tools.exchange_calendar_tool import ExchangeCalendarTool

TOOLS = [ExchangeCalendarTool]           # clases ITool (no instancias)
SKILLS = ["exchange_calendar.yaml"]      # paths relativas al manifest

# Ejemplo: ~/.inaki/ext/run_shell/manifest.py
from ext.run_shell.tools.run_shell_tool import RunShellTool

TOOLS = [RunShellTool]
SKILLS = ["run_shell.yaml"]
```

Los imports en los manifests usan `ext.<name>.tools.*` porque `~/.inaki/` está en `sys.path` — la carpeta `~/.inaki/ext/` actúa como paquete Python.

### 3. container.py — _register_extensions() completo

```python
def _register_extensions(self, ext_dirs: list[str]) -> None:
    """
    Auto-discovery de extensiones de usuario.

    Itera sobre cada directorio en ext_dirs en orden, escanea */manifest.py,
    y registra TOOLS + SKILLS declarados. Usa spec_from_file_location para
    cargar por path absoluta sin dependencia de sys.path para el manifest.
    Añade el parent de cada ext_dir a sys.path para que los imports internos
    del engine de cada extensión resuelvan.
    """
    import importlib.util
    import sys
    from pathlib import Path

    for ext_dir_str in ext_dirs:
        ext_dir = Path(ext_dir_str).expanduser().resolve()

        if not ext_dir.exists() or not ext_dir.is_dir():
            logger.debug("Directorio de extensiones no encontrado: %s", ext_dir)
            continue

        # Añadir parent al sys.path para que los imports del engine resuelvan
        parent_str = str(ext_dir.parent)
        if parent_str not in sys.path:
            sys.path.insert(0, parent_str)
            logger.debug("sys.path += %s (extensiones en %s)", parent_str, ext_dir.name)

        for manifest_path in sorted(ext_dir.glob("*/manifest.py")):
            ext_name = manifest_path.parent.name
            # ID único para evitar colisión entre extensiones de mismo nombre en dirs distintos
            module_id = f"_inaki_ext_{ext_dir.name}_{ext_name}_manifest"

            try:
                spec = importlib.util.spec_from_file_location(module_id, manifest_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as exc:
                logger.warning(
                    "Extensión '%s': falló al cargar manifest (%s) — skipping",
                    ext_name, exc,
                )
                continue

            # Registrar tools
            for tool_cls in getattr(module, "TOOLS", []) or []:
                try:
                    tool_instance = tool_cls()
                    # Verificar colisión de nombres antes de registrar
                    if tool_instance.name in self._tools._tools:
                        logger.warning(
                            "Extensión '%s': tool '%s' ya registrada — skipping (colisión)",
                            ext_name, tool_instance.name,
                        )
                        continue
                    self._tools.register(tool_instance)
                    logger.info(
                        "Extensión '%s': tool '%s' registrada",
                        ext_name, tool_instance.name,
                    )
                except Exception as exc:
                    logger.warning(
                        "Extensión '%s': falló al instanciar %r (%s) — skipping tool",
                        ext_name, tool_cls, exc,
                    )

            # Registrar skills
            for skill_rel in getattr(module, "SKILLS", []) or []:
                skill_path = (manifest_path.parent / skill_rel).resolve()
                if not skill_path.exists():
                    logger.warning(
                        "Extensión '%s': skill file no encontrado: %s",
                        ext_name, skill_path,
                    )
                    continue
                self._skills.add_file(skill_path)
                logger.info(
                    "Extensión '%s': skill '%s' añadida",
                    ext_name, skill_path.name,
                )
```

### 4. container.py — _register_tools() y __init__ modificados

```python
def _register_tools(self) -> None:
    """Registra tools built-in del núcleo. Las extensiones se cargan aparte."""
    from adapters.outbound.tools.patch_file_tool import PatchFileTool
    from adapters.outbound.tools.read_file_tool import ReadFileTool
    from adapters.outbound.tools.web_search_tool import WebSearchTool
    from adapters.outbound.tools.write_file_tool import WriteFileTool

    self._tools.register(WebSearchTool())
    self._tools.register(ReadFileTool())
    self._tools.register(WriteFileTool())
    self._tools.register(PatchFileTool())
```

Removidos: `ShellTool` (→ extensión `run_shell`) y `ExchangeCalendarTool` (→ extensión `exchange_calendar`).

Invocación en `__init__`:
```python
self._tools = ToolRegistry(embedder=self._embedder)
self._register_tools()
self._register_extensions(global_config.app.ext_dirs)
```

### 5. config.py — AppConfig

```python
class AppConfig(BaseModel):
    name: str = "Iñaki"
    log_level: str = "INFO"
    data_dir: str = "data"
    models_dir: str = "models"
    skills_dir: str = "skills"
    ext_dirs: list[str] = ["ext", "~/.inaki/ext"]   # reemplaza ext_dir: str
    default_agent: str = "general"
```

El campo es una lista de strings (no Paths) para ser serializable en YAML sin conversión especial. La expansión se hace en runtime con `Path.expanduser().resolve()`.

### 6. YamlSkillRepository — implementación completa

```python
class YamlSkillRepository(ISkillRepository):

    def __init__(self, skills_dir: str, embedder: IEmbeddingProvider) -> None:
        self._skills_dir = Path(skills_dir)
        self._embedder = embedder
        self._extra_files: list[Path] = []
        self._skills: list[Skill] = []
        self._embeddings: list[list[float]] = []
        self._loaded = False

    def add_file(self, path: Path) -> None:
        """Registra un YAML de skill adicional fuera de skills_dir. Invalida cache."""
        path = Path(path).resolve()
        if path in [p.resolve() for p in self._extra_files]:
            return
        self._extra_files.append(path)
        self._loaded = False

    async def _load_skill_from_path(self, yaml_file: Path) -> None:
        try:
            with yaml_file.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            skill = Skill(
                id=data.get("id", yaml_file.stem),
                name=data.get("name", yaml_file.stem),
                description=data.get("description", ""),
                instructions=data.get("instructions", ""),
                tags=data.get("tags", []),
            )
            text = f"{skill.name} {skill.description} {' '.join(skill.tags)}"
            embedding = await self._embedder.embed_passage(text)
            self._skills.append(skill)
            self._embeddings.append(embedding)
            logger.debug("Skill cargada: '%s' (%s)", skill.id, yaml_file)
        except Exception as exc:
            logger.warning("Error cargando skill %s: %s", yaml_file, exc)

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        self._skills = []
        self._embeddings = []
        seen: set[Path] = set()

        # 1. Directorio base (built-ins)
        if self._skills_dir.exists():
            for yaml_file in sorted(self._skills_dir.rglob("*.yaml")):
                resolved = yaml_file.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    await self._load_skill_from_path(yaml_file)
        else:
            logger.warning("Directorio de skills no encontrado: %s", self._skills_dir)

        # 2. Archivos extra (desde extensiones)
        for extra in self._extra_files:
            resolved = extra.resolve()
            if resolved in seen:
                logger.debug("Skill extra ya cargada: %s", extra)
                continue
            seen.add(resolved)
            await self._load_skill_from_path(extra)

        logger.info("YamlSkillRepository: %d skill(s) cargada(s)", len(self._skills))
        self._loaded = True

    async def list_all(self) -> list[Skill]:
        await self._ensure_loaded()
        return list(self._skills)

    async def retrieve(self, query_embedding: list[float], top_k: int = 3) -> list[Skill]:
        await self._ensure_loaded()
        if not self._skills:
            return []
        scored = [
            (skill, _cosine_similarity(query_embedding, emb))
            for skill, emb in zip(self._skills, self._embeddings)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [skill for skill, _ in scored[:top_k]]
```

### 7. Manifests de extensiones personales

**`~/.inaki/ext/exchange_calendar/manifest.py`:**
```python
from __future__ import annotations
from ext.exchange_calendar.tools.exchange_calendar_tool import ExchangeCalendarTool

TOOLS = [ExchangeCalendarTool]
SKILLS = ["exchange_calendar.yaml"]
```

**`~/.inaki/ext/exchange_calendar/exchange_calendar.yaml`:**
```yaml
id: "exchange_calendar"
name: "Calendario Exchange"
description: "Gestiona reuniones, citas y disponibilidad en Microsoft Exchange/Outlook"
instructions: |
  Cuando el usuario pregunte sobre reuniones, disponibilidad de colegas, o quiera
  crear/modificar/eliminar eventos en su calendario de Outlook/Exchange, usa la
  herramienta exchange_calendar. Antes del primer uso configurar credenciales con
  operation=configure. Si no conocés el email exacto de un colega, usá operation=resolve.
tags:
  - "calendario"
  - "exchange"
  - "outlook"
  - "reuniones"
  - "disponibilidad"
```

**`~/.inaki/ext/run_shell/manifest.py`:**
```python
from __future__ import annotations
from ext.run_shell.tools.run_shell_tool import RunShellTool

TOOLS = [RunShellTool]
SKILLS = ["run_shell.yaml"]
```

**`~/.inaki/ext/run_shell/run_shell.yaml`:**
```yaml
id: "run_shell"
name: "Shell"
description: "Ejecuta comandos shell en el sistema local"
instructions: |
  Cuando el usuario pida ejecutar un comando, script, o explorar el sistema de archivos,
  usa la herramienta run_shell. NUNCA usar flags destructivos sin confirmación explícita
  del usuario. Reportar el output verbatim sin interpretarlo.
tags:
  - "shell"
  - "comandos"
  - "terminal"
  - "sistema"
```

### 8. Migration path

Secuencia que mantiene el repo en estado verde en cada paso:

**Pre-condición**: verificar `.gitignore` — `ext/` no debe estar ignorado; `~/.inaki/` no aplica (fuera del repo).

1. **Refactorizar `YamlSkillRepository`** (sección 6) — cambio aditivo. `pytest` pasa.

2. **Actualizar `AppConfig`** — `ext_dir: str` → `ext_dirs: list[str]`. Verificar que `load_global_config` y `load_agent_config` manejan el campo como lista en el YAML merge.

3. **Crear estructura `~/.inaki/ext/`**:
   - `~/.inaki/ext/__init__.py` (vacío)
   - Subdirectorios `exchange_calendar/` y `run_shell/` con sus `__init__.py`

4. **Copiar y adaptar `exchange_calendar`**:
   - Copiar `adapters/outbound/tools/exchange_calendar/*.py` → `~/.inaki/ext/exchange_calendar/tools/engine/*.py`
   - Reescribir imports de `adapters.outbound.tools.exchange_calendar.*` → `ext.exchange_calendar.tools.engine.*`
   - Copiar `exchange_calendar_tool.py` → `~/.inaki/ext/exchange_calendar/tools/exchange_calendar_tool.py`
   - Crear manifest + YAML
   - Smoke test: `PYTHONPATH=~/.inaki python -c "from ext.exchange_calendar.tools.exchange_calendar_tool import ExchangeCalendarTool"`

5. **Copiar y adaptar `run_shell`**:
   - Copiar `ext/run_shell/tools/run_shell_tool.py` → `~/.inaki/ext/run_shell/tools/run_shell_tool.py`
   - Crear manifest + YAML

6. **Limpiar `./ext/`**:
   - Eliminar `ext/run_shell/` completo
   - Eliminar `ext/web_search/` completo (scaffolding huérfano)
   - Mantener `ext/__init__.py` (directorio de proyecto válido aunque vacío)

7. **Switch atómico en container**:
   - Remover `ShellTool` y `ExchangeCalendarTool` de `_register_tools()`
   - Añadir `_register_extensions(global_config.app.ext_dirs)`
   - Activar manifests en `~/.inaki/ext/`
   - `pytest` + arranque manual: confirmar logs `"Extensión 'exchange_calendar': tool registrada"` y `"Extensión 'run_shell': tool registrada"`

8. **Eliminar código viejo**:
   - `adapters/outbound/tools/shell_tool.py`
   - `adapters/outbound/tools/exchange_calendar_tool.py`
   - `adapters/outbound/tools/exchange_calendar/` (completo)
   - Grep final: `grep -rn "adapters.outbound.tools.exchange_calendar\|shell_tool\|ShellTool" .` → cero matches en código (excluir `__pycache__`)

9. **Verificación final**: suite completa + smoke test manual.

## Testing Strategy

No existen tests actuales para los archivos afectados. Tests nuevos son adiciones puras.

### `tests/unit/adapters/test_yaml_skill_repo_add_file.py`

Fixture: `FakeEmbedder` con `async def embed_passage(text)` → `[1.0, 0.0, 0.0]`.

- `test_add_file_loads_skill` — YAML válido → aparece en `list_all()`
- `test_add_file_combines_with_dir` — `skills_dir` + `add_file` → ambas presentes
- `test_add_file_invalidates_cache` — `list_all()`, `add_file()`, `list_all()` → N+1
- `test_add_file_deduplicates` — misma path dos veces → una carga
- `test_add_file_deduplicates_with_dir` — extra ya en `skills_dir` → no duplicado
- `test_add_file_missing_path_no_crash` — path inexistente → warning, `list_all()` no falla

### `tests/unit/infrastructure/test_container_extensions.py`

Fixture: `tmp_path` + monkeypatch de `sys.path` + limpieza de `sys.modules` entre tests.

- `test_happy_path` — manifest con `TOOLS` + `SKILLS` → registrados
- `test_missing_dir` — dir inexistente → no-op sin error
- `test_malformed_manifest` — `SyntaxError` → warning, resto procesa
- `test_import_error_manifest` — `ImportError` → warning, resto procesa
- `test_empty_manifest` — sin `TOOLS`/`SKILLS` → no crash
- `test_tool_instantiation_error` — `ToolClass()` tira → warning, no aborta
- `test_missing_skill_file` — YAML declarado no existe → warning, no añade
- `test_multiple_dirs_order` — dos dirs, extensión en cada uno → orden correcto
- `test_name_collision_warning` — tool con nombre ya registrado → warning, original intacto

### `tests/unit/infrastructure/test_container_builtin_tools.py`

- Regression guard: `web_search`, `read_file`, `write_file`, `patch_file` están registradas
- `shell` / `run_shell` / `exchange_calendar` NO están en built-ins (cuando `ext_dirs=[]`)

## Dependency Order

1. `YamlSkillRepository.add_file()` — aditivo, no rompe nada
2. `AppConfig.ext_dirs` — trivial, default backward-compatible
3. Crear estructura `~/.inaki/ext/` + copiar código adaptado
4. Crear manifests + YAMLs de skills
5. Switch en container (commit atómico)
6. Eliminar código viejo
7. Verificar `.gitignore` y correr suite completa
