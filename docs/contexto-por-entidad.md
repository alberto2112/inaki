# Contexto por entidad (memoria caliente) — `~/.inaki/users/`

Cada turno del agente inyecta un bloque opcional de "contexto de la entidad" en
el system prompt. Antes existía un único `~/.inaki/USER.md` global; hoy se
resuelve por canal y por **entidad de conversación**:

```
~/.inaki/users/{channel_type}/_common.md       ← común al canal (se inyecta ANTES)
~/.inaki/users/{channel_type}/{context_id}.md  ← archivo de la entidad
(nada)                                         ← si ninguno existe
```

`context_id` es la clave estable de la conversación: `chat_id or user_id`
(`ChannelContext.context_id`). **Misma resolución en privado y en grupo** — un
grupo no tiene "el" usuario, tiene un `chat_id`; un privado tiene su `chat_id`
propio; los canales que no distinguen chat de usuario (CLI/REST) caen a `user_id`.

Esa MISMA clave se expone al LLM como la variable `{{CHANNEL.CONTEXTID}}` (ver
[`prompt_builder.md`](prompt_builder.md)): así el operador puede decirle al agente
*"tu memoria caliente vive en `~/.inaki/users/{{CHANNEL}}/{{CHANNEL.CONTEXTID}}.md`,
editala con `write_file`"* y el archivo que el LLM escribe es EXACTAMENTE el que
`_read_user_context` lee el turno siguiente. Lectura y escritura no divergen.

- **`_common.md` — contexto común al canal**: si existe, su contenido se
  concatena **antes** del archivo de la entidad. Pensado para reglas que aplican a
  toda entidad del canal, p. ej. formato de respuesta ("no uses tablas markdown en
  Telegram, no las renderiza"). El prefijo `_` evita colisión con un
  `{context_id}.md`. Se inyecta aunque no haya archivo de entidad.
- **Scope por canal**: un mismo `context_id` en Telegram ≠ en CLI. Cada canal
  tiene su propio subdirectorio. `_common.md` también es por canal — el de CLI
  no se hereda en Telegram.
- **`context_id` es estable, no legible**: la clave es un ID (chat_id/user_id), no
  el `@username`. Se prefirió estabilidad y consistencia lectura/escritura por
  sobre legibilidad — el username muta y no existe en grupos. `username` sigue
  poblado pero SOLO alimenta la variable `{{CHANNEL.USERNAME}}`, no el nombre del
  archivo.
- **Sin archivo → contexto vacío**. No hay fallback global ni warning. Pensado
  para uso doméstico: si una entidad no tiene archivo, el agente la trata sin
  contexto previo.
- **Anti path traversal**: si `context_id` contiene `/`, `\` o `..`, el candidato
  se descarta → contexto vacío.

## Cómo cada canal resuelve `channel_type` y `context_id`

| Canal | `channel_type` | `context_id` (= `chat_id or user_id`) |
|-------|----------------|----------------------------------------|
| Telegram (privado) | `telegram` | `chat_id` del privado (== `from_user.id`) |
| Telegram (grupo)   | `telegram` | `chat_id` del grupo (negativo, p. ej. `-1001234567890`) |
| CLI (admin chat)   | `cli`      | `chat_id` (si el cliente lo manda) → `channels.cli.user` (si está) → `session_id` (UUID efímero) |
| REST `/chat/turn`  | `cli`      | igual que CLI |

## Ejemplo de archivo

`~/.inaki/users/telegram/-1001234567890.md` (un grupo) o
`~/.inaki/users/telegram/6027834521.md` (un privado):

```markdown
Contexto de esta conversación.
- Stack preferido: Python + hexagonal architecture.
- Idioma: español rioplatense (voseo).
- Evitá disclaimers innecesarios.
```

> El `chat_id` de un grupo se obtiene con `/chatid` dentro del grupo (ver
> `telegram-group-auth` en `CLAUDE.md`). Para ver el `context_id` de cualquier
> turno, `inaki inspect` imprime el prompt final ya resuelto.

## Auto-creación de subdirectorios

El daemon, al arrancar, crea `~/.inaki/users/{channel}/` por cada canal
configurado en cualquier agente (`AgentConfig.channels.keys()`). No hace falta
hacer `mkdir` manual — basta entrar a `~/.inaki/users/` y aparecen los
subdirectorios listos para depositar archivos de contexto.

## Migración

Dos cortes históricos, ambos sin auto-migración (uso doméstico):

1. **Desde `~/.inaki/USER.md`** (global → por canal/entidad): el path legacy ya
   **no se lee**.
2. **Desde `{username}.md` → `{context_id}.md`** (clave única): los archivos
   privados nombrados por `@username` dejan de leerse. Renombralos al `chat_id` del
   privado y cambiá la variable del system prompt a `{{CHANNEL.CONTEXTID}}`.

```bash
# si venías de USER.md global:
mv ~/.inaki/USER.md ~/.inaki/users/telegram/<chat_id>.md
# si venías de la resolución por username:
mv ~/.inaki/users/telegram/alberto.md ~/.inaki/users/telegram/<chat_id>.md
```
