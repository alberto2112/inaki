# Instance home — `--home` / `INAKI_HOME`

Dónde vive una instancia entera y cómo se resuelve cada path de runtime.
Para relocalizar campos sueltos, ver [`config-reference.md`](config-reference.md).

## El knob único

Por defecto todo cuelga de **`~/.inaki/`**. Un solo knob relocaliza la instancia
*entera* — config, datos (DBs), `secret.key`, `tool_config.yaml`, `users/` y el
índice de knowledge:

```bash
inaki --home /srv/inaki-deptB daemon      # flag
INAKI_HOME=/srv/inaki-deptB inaki daemon  # env var (systemd: Environment=INAKI_HOME=...)
```

Orden de resolución (`infrastructure/home.py`): flag `--home` → env `INAKI_HOME`
→ default `~/.inaki`. Con `--home /foo`, los paths se re-anclan a `/foo/config`,
`/foo/data/*.db`, `/foo/knowledge/`, `/foo/users/`, `/foo/secret.key`,
`/foo/config/tool_config.yaml`.

**Este es el límite de aislamiento de los recursos harness-global.** `knowledge`,
`scheduler` y `faces`/`photos` son singletons por proceso (ver "Tiers de recursos"
en [`arquitectura.md`](arquitectura.md)) — para aislar uno, corré un **segundo
proceso del arnés con su propio `--home`**.

**Los puertos NO se derivan del home.** Una segunda instancia tiene que fijar su
propio `admin.port` y `broadcast.server.port` (si lo usa) en su YAML para no
colisionar con la primera.

Compatibilidad: el default `~/.inaki` no cambió, así que las instalaciones de una
sola instancia no necesitan migración. El viejo flag `--config DIR` fue
reemplazado por `--home` (corte limpio, sin alias).

## Resolución de paths

Los campos de path de runtime (`*_filename`, `*_dirname`) se resuelven así:

- **Paths relativos** (p. ej. `"data/inaki.db"`) se anclan bajo el home.
- **Paths absolutos** (p. ej. `"/srv/inaki/data/inaki.db"`) se usan tal cual.
- **Tildes** (`~/...`) se expanden al home del usuario.
- El valor especial de SQLite `:memory:` pasa sin interpretarse como path.

Layout por defecto:

```
~/.inaki/
├── config/            # global.yaml (credenciales incluidas) + tool_config.yaml
├── agents/            # YAML por agente (tokens incluidos)
├── data/              # DBs SQLite (inaki.db, history.db, scheduler.db, embedding_cache.db)
├── models/            # Modelos ONNX (p. ej. e5-small/)
├── mem/               # Digest markdown — un fichero por scope (digest_{channel}_{chat_id}.md)
├── users/             # Contexto por entidad (ver contexto-por-entidad.md)
└── ext/               # Extensiones de usuario
```

Si necesitás mover el almacenamiento a otra raíz sin mover la instancia entera
(p. ej. un disco dedicado en la Pi 5), pasá paths absolutos en `global.yaml`:

```yaml
embedding:
  model_dirname: "/srv/inaki/models/e5-small"
  cache_filename: "/srv/inaki/data/embedding_cache.db"

memories:
  db_filename: "/srv/inaki/data/inaki.db"
  digest_filename: "/srv/inaki/mem/digest_{channel}_{chat_id}.md"

chat_history:
  db_filename: "/srv/inaki/data/history.db"

scheduler:
  db_filename: "/srv/inaki/data/scheduler.db"
```
