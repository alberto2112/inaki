# Fallos preexistentes en el test suite

Estos fallos existían **antes** de la implementación del embedding cache. No son causados por ese cambio.

Total: 8 errores de colección + 15 fallos de test.

---

## Grupo 1 — Errores de colección (8 archivos)

### Causa raíz

`ImportError: cannot import name 'TooManyActiveTasksError' from 'core.domain.errors'`

La clase `TooManyActiveTasksError` se referencia en varios archivos pero no existe en `core/domain/errors.py`. Fue importada o planificada pero nunca implementada (o fue eliminada sin actualizar los imports).

### Archivos afectados

```
tests/unit/adapters/tools/test_scheduler_tool.py
tests/unit/infrastructure/test_container.py
tests/unit/infrastructure/test_container_builtin_tools.py
tests/unit/infrastructure/test_container_extensions.py
tests/unit/infrastructure/test_container_wire_scheduler.py
tests/unit/use_cases/test_delegation_integration.py
tests/unit/use_cases/test_schedule_task.py
tests/unit/use_cases/test_schedule_task_guardrail.py
```

### Fix sugerido

**Opción A (preferida)**: Agregar la clase en `core/domain/errors.py`:

```python
class TooManyActiveTasksError(DomainError):
    """Se excedió el límite de tareas activas simultáneas."""
    pass
```

**Opción B**: Si la clase ya no es necesaria, eliminar todos los imports de los 8 archivos afectados y ajustar la lógica que dependía de ella.

---

## Grupo 2 — Fallo de migración de historial (1 test)

### Causa raíz

`sqlite3.OperationalError: no such column: infused`

El test `test_migration_adds_infused_column_and_marks_existing_rows` en `tests/unit/adapters/test_sqlite_history_store.py` verifica que `SQLiteHistoryStore` detecte una DB legacy (sin la columna `infused`) y la migre automáticamente via `ALTER TABLE`. Sin embargo, `_ensure_schema()` solo ejecuta `CREATE TABLE IF NOT EXISTS` — si la tabla ya existe sin la columna `infused`, la sentencia no la agrega.

### Archivo afectado

```
tests/unit/adapters/test_sqlite_history_store.py
  └── test_migration_adds_infused_column_and_marks_existing_rows
```

### Fix sugerido

Agregar lógica de migración en `_ensure_schema()` de `adapters/outbound/history/sqlite_history_store.py`:

```python
async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
    await conn.execute(_CREATE_TABLE)
    await conn.execute(_CREATE_INDEX)
    await conn.execute(_CREATE_INFUSED_INDEX)

    # Migración: agregar columna infused si no existe (DB legacy)
    try:
        await conn.execute("ALTER TABLE history ADD COLUMN infused INTEGER NOT NULL DEFAULT 1")
        await conn.commit()
        logger.info("Migración: columna 'infused' agregada, filas existentes marcadas como infused=1")
    except Exception:
        pass  # La columna ya existe — normal en DBs nuevas

    await conn.commit()
```

Nota: el DEFAULT 1 en el ALTER TABLE marca las filas existentes como ya infused (estado estable previo), que es exactamente lo que el test espera.

---

## Grupo 3 — Columna `created_by` faltante en scheduler (5 tests)

### Causa raíz

`sqlite3.OperationalError: table tasks has no column named created_by`

La tabla `scheduled_tasks` en el schema actual de `sqlite_scheduler_repo.py` incluye la columna `created_by`, pero los tests en `test_sqlite_scheduler_created_by.py` fallan porque aparentemente trabajan con una DB que tiene el schema viejo (sin esa columna), o hay un mismatch entre el nombre de la tabla en el schema (`scheduled_tasks`) y el que esperan los tests (`tasks`).

### Archivos afectados

```
tests/unit/adapters/test_sqlite_scheduler_created_by.py  (5 tests)
```

### Fix sugerido

Revisar el archivo `test_sqlite_scheduler_created_by.py` para determinar si:

1. Los tests referencian la tabla como `tasks` en lugar de `scheduled_tasks` (nombre incorrecto)
2. O el repo de scheduler necesita la misma lógica de migración que el history store: detectar schema viejo y ejecutar `ALTER TABLE scheduled_tasks ADD COLUMN created_by TEXT DEFAULT ''`

Lo más probable es opción 2: la columna fue agregada en un commit posterior y los tests de migración verifican que el store la agrega automáticamente si falta.

---

## Grupo 4 — `UserConfig` no definida en config.py (6 tests)

### Causa raíz

`NameError: name 'UserConfig' is not defined`

En `infrastructure/config.py`, la función `_render_default_global_yaml()` referencia `UserConfig().model_dump()` en la línea 319, pero la clase `UserConfig` no existe en ese archivo ni es importada desde ningún lugar.

```python
# línea 319 de infrastructure/config.py — BUG
"user": UserConfig().model_dump(),
```

### Archivos afectados

```
tests/unit/infrastructure/test_config.py                 (3 tests)
tests/unit/infrastructure/test_ensure_user_config.py     (3 tests)
```

Cualquier test que llame a `ensure_user_config()` o `_render_default_global_yaml()` explota al llegar a esa línea.

### Fix sugerido

**Opción A**: Si `UserConfig` fue eliminada en el refactor a YAML, simplemente remover esa línea del dict `defaults`:

```python
# Eliminar:
"user": UserConfig().model_dump(),
```

**Opción B**: Si `UserConfig` debe existir, definirla en `infrastructure/config.py` como un modelo Pydantic con los campos que correspondan y asegurarse de que `GlobalConfig` la incluya.

La opción A es la más probable — parece ser un residuo del refactor que eliminó `UserConfig` del dominio pero olvidó limpiar `_render_default_global_yaml()`.

---

## Resumen

| Grupo | Tests afectados | Root cause | Archivo a corregir |
|-------|----------------|------------|--------------------|
| `TooManyActiveTasksError` faltante | 8 (colección) | Clase nunca definida | `core/domain/errors.py` |
| Migración columna `infused` | 1 | `_ensure_schema` no hace `ALTER TABLE` | `adapters/outbound/history/sqlite_history_store.py` |
| Columna `created_by` en scheduler | 5 | Schema viejo sin migración | `adapters/outbound/scheduler/sqlite_scheduler_repo.py` |
| `UserConfig` no definida | 6 | Referencia a clase eliminada | `infrastructure/config.py` línea 319 |
