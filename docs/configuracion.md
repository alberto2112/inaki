# Configuración — índice

Puerta de entrada a la config de Inaki. **Este documento no lista campos**: la
referencia de cada parámetro se autogenera desde los docstrings del schema
Pydantic y vive en [`config-reference.md`](config-reference.md).

> **NUNCA** documentes un parámetro fuera de su docstring en el schema
> (`infrastructure/config_schema.py`): de ahí salen `config-reference.md`,
> `global.example.yaml` y la ayuda del setup TUI (`inaki gen-docs` los regenera,
> y un test de drift los vigila). Cualquier otra copia nace condenada a divergir.

## Adónde ir

| Necesitás | Documento |
|---|---|
| Qué significa un campo, su tipo y su default | [`config-reference.md`](config-reference.md) — **autogenerado** |
| Relocalizar la instancia, o cómo resuelve un path | [`instance-home.md`](instance-home.md) |
| Configurar una tool (API keys de `web_search`, `exchange`…) | [`tool-config-protocol.md`](tool-config-protocol.md) |
| Memoria caliente por conversación (`~/.inaki/users/`) | [`contexto-por-entidad.md`](contexto-por-entidad.md) |
| Acotar a qué paths llegan las file tools | [`workspace-containment.md`](workspace-containment.md) |
| Transcripción de voz en Telegram | [`transcripcion.md`](transcripcion.md) |
| Endpoints HTTP del daemon | [`admin-api.md`](admin-api.md) |
| Editar la config a mano vs. por TUI | [`setup-tui-smoke.md`](setup-tui-smoke.md) |
| Broadcast entre Pis | [`broadcast-smoke.md`](broadcast-smoke.md) |
| Fuentes de conocimiento (RAG) | [`knowledge.md`](knowledge.md) |
| Consolidación y reconciliación de memoria | [`flujo_ejecucion.md`](flujo_ejecucion.md) |
| Reconocimiento facial | [`face-recognition.md`](face-recognition.md) |
| Ruteo de salidas del scheduler (`channel_fallback`) | [`scheduler-spec.md`](scheduler-spec.md) |

## Qué config está usando el sistema

Antes de editar nada, así se le pregunta al sistema **qué config está usando de
verdad**, y de dónde sale cada valor:

```bash
inaki config show --agent dev --origin   # config efectiva + capa de origen
inaki config show --secrets              # qué credenciales están puestas y cuáles faltan
inaki config show --json                 # legible por máquina
```

```
   llm.model                modelo-propio   [agent]
   llm.provider             groq            [global]
   llm.temperature          0.7             [default]
🔒 providers.groq.api_key   ********        [global]
```

Tres capas aparecen como origen: `default` (lo que aplica el schema cuando nadie
declara nada), `global` y `agent`. Un valor con origen `default` **no está
escrito en ningún YAML** — por eso leer los ficheros a mano no responde la
pregunta.

**Los secretos siempre salen redactados.** Los campos marcados como credencial en
el schema se emiten como `********`, nunca en claro: la salida está pensada para
pegarse en un issue. `--secrets` la acota a las credenciales, marcando las que
siguen `(sin configurar)` — pero solo de las secciones que ya declaraste, así no
se llena de campos pendientes de features que no usás.

## Los ficheros

| Fichero | ¿Commiteable? | Para qué |
|---|---|---|
| `config/global.yaml` | ❌ no | Config base del sistema **y** registry de credenciales compartidas (`providers.<name>.api_key`, `admin.auth_key`). Modo `600` |
| `agents/{id}.yaml` | ❌ no | Config del agente: id, name, description, system_prompt, overrides, canales — tokens incluidos. Modo `600` |
| `config/tool_config.yaml` | ❌ no | Store del Tool Config Protocol (del daemon, con secretos `enc:`). **No participa del merge** |
| `config/global.example.yaml` | ✅ sí | Referencia canónica autogenerada, sin valores reales |

Solo el `*.example.yaml` es commiteable. `global.yaml` y `agents/{id}.yaml`
llevan credenciales vivas — **nunca los commitees**.

## Las 2 capas

La config final de cada agente se arma fundiendo dos ficheros en orden. Cada capa
pisa **solo los campos que declara**; nunca borra un campo heredado por estar ausente.

```
config/global.yaml     (1) base del sistema + credenciales compartidas
    ↓ merge campo a campo
agents/{id}.yaml       (2) config del agente + sus propias credenciales
    ↓
AgentConfig resuelto y completo
```

La semántica exacta (dict⊕dict funde, lista reemplaza, `null` pisa, sentinel
borra, cambiar de forma entre capas es error) vive **una sola vez**, en
`core/domain/config_merge.py`. Es el motor único de los cuatro carriles: carga,
edición del setup TUI, `get_effective_config` y sub-agentes efímeros.

**Secreto es una marca del schema, no un fichero.** Un campo es secreto porque el
schema Pydantic lo dice (`kind == "secret"`) — eso es lo que lo enmascara en el
TUI. No tiene nada que ver con en qué fichero vive. El layout viejo de 4 capas
(`*.secrets.yaml`) se plegó a 2; el daemon lo migra solo al arrancar y el
operador no tiene nada que hacer. → `secrets-layer-eradication` en
[`migraciones.md`](migraciones.md).

## Credenciales

No hay fichero de credenciales aparte. El registry vive bajo `providers:` en
`config/global.yaml`, al lado de todo lo demás:

```yaml
providers:
  openrouter:
    api_key: "sk-or-..."
  groq:
    api_key: "gsk_..."
```

Un agente que necesita otra key para el mismo vendor redefine la entrada en su
propio `agents/{id}.yaml` — el merge completa campo a campo, así que no hace
falta repetir `base_url` ni `type`.

## Agregar un agente

1. Creá `agents/miagente.yaml` con `id`, `name`, `description`, `system_prompt` y
   los tokens que haga falta (p. ej. `channels.telegram.token`) — un solo fichero.
2. Reiniciá el daemon: `systemctl restart inaki`.

El `AgentRegistry` escanea `agents/*.yaml` al arrancar. No hace falta registro
manual ni tocar código.
