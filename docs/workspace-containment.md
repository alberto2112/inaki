# `workspace` — contención de paths para las file tools

Cada agente puede declarar un `workspace` que controla a qué paths pueden
acceder las tools de fichero. Se configura en `agents/{id}.yaml`:

```yaml
workspace:
  path: "/Users/alberto/tmp/mi_workspace"  # Directorio raíz permitido
  containment: "strict"                    # strict | warn | off
```

## Modos de contención

| Modo | Comportamiento |
|------|----------------|
| `strict` | Bloquea cualquier path fuera de `workspace.path`. La tool devuelve un error al LLM. **Default.** |
| `warn` | Permite paths fuera del workspace pero loguea un WARNING. Útil para depurar. |
| `off` | Sin restricciones. La tool accede a cualquier path del sistema. |

## Tools afectadas

| Tool | ¿Sandboxed? |
|------|-------------|
| `read_file` | ✅ sí |
| `write_file` | ✅ sí |
| `patch_file` | ✅ sí |
| `edit_file` | ✅ sí |
| `shell_exec` | ❌ no — ejecuta comandos sin restricción de paths |
| `delegate`, `scheduler`, resto de builtins | ❌ no aplica |

> ⚠ `shell_exec` es una extensión de `ext/` y no tiene contención de ningún tipo.
> Si el LLM puede llamar a `shell_exec`, puede operar sobre cualquier path del
> sistema.

Si `workspace.path` no está definido en la config del agente, se usa el
directorio de trabajo del proceso al arrancar. Para evitar ambigüedades en
producción (systemd), especificá siempre un path absoluto.
