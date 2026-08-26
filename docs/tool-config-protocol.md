# Tool Config Protocol — `config/tool_config.yaml`

Credenciales y ajustes de las tools (builtin y de `ext/`) viven en su **propio
fichero**, `config/tool_config.yaml`, bajo una raíz `tool_config:` con un
namespace por tool.

Este fichero es **del daemon**: el store lo lee al arrancar y lo reescribe en
cada `configure`. Se mantiene **separado de `global.yaml`** — ese es tuyo,
escrito a mano (`providers.*`, tokens), y el daemon no lo toca nunca. **No
participa del merge de 2 capas.**

**El usuario puede configurar tools desde cualquier canal hablándole al agente**
— la tool expone `operation=configure` y persiste acá vía el protocolo
(`IToolConfigStore`); editar el YAML a mano también funciona.

```yaml
tool_config:
  web_search:
    api_key: "enc:gAAAA..."     # cifrada en reposo (Fernet, clave en ~/.inaki/secret.key)
    search_depth: basic         # los campos no sensibles quedan en plano
    max_results: 5
  exchange:
    username: "dominio\\alberto"
    password: "enc:gAAAA..."
    mail: alberto@empresa.com
    ews_url: https://mail.empresa.com/EWS/Exchange.asmx
    timezone: Europe/Madrid
```

## Cómo funciona

Una tool declara `config_namespace` (class attr) y el container le inyecta el
store en construcción. Las escrituras de `configure` tienen efecto inmediato (sin
reinicio) y sobreviven reinicios.

Los campos sensibles se cifran en reposo con la clave autogenerada en
`~/.inaki/secret.key` (0600) y `show_config` los muestra enmascarados.

> **Honestidad sobre el modelo de amenaza:** clave y datos comparten disco. El
> cifrado protege contra una divulgación accidental del YAML, no contra un
> atacante con acceso al filesystem.

**Respaldá `secret.key`**: si se pierde, los valores cifrados son irrecuperables
(la tool simplemente te va a pedir que la configures de nuevo).

Sin credenciales la tool igual se registra; el LLM recibe un error
`CONFIGURATION REQUIRED` que le indica preguntarle al usuario y llamar a
`configure` — esa es la UX conversacional del protocolo.

Consumidores actuales: `web_search` (builtin), `exchange_calendar`/`exchange_mail`,
`fal_music`, `replicate_music` (ext).

## Migración desde versiones previas

Builds anteriores guardaban `tool_config:` dentro del legacy
`global.secrets.yaml`. Al primer arranque el daemon mueve ese bloque a
`config/tool_config.yaml` automáticamente (`migrate_tool_config_to_own_file`),
antes de que el sidecar de secrets se pliegue sobre `global.yaml`, así el bloque
nunca aterriza en la capa principal. `secret.key` no cambia, así que los valores
cifrados (`enc:`) siguen desencriptando — no hace falta reconfigurar nada.

> **NUNCA** crees un YAML de config propio por tool: el store compartido por
> namespace ya existe. → `tool-config-protocol` en [`migraciones.md`](migraciones.md)
