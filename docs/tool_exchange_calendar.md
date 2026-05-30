# Tool: exchange_calendar

Integration with Microsoft Exchange for calendar management via EWS (Exchange Web Services).
Allows reading, searching, creating, updating, and deleting Outlook calendar events from the agent.

---

## Relevant Files

| File | Role |
|------|------|
| `adapters/outbound/tools/exchange_calendar_tool.py` | Facade: validation, schema, routing to the engine |
| `adapters/outbound/tools/exchange_calendar/engine.py` | Core logic: accounts, operation dispatch |
| `adapters/outbound/tools/exchange_calendar/config_store.py` | Credential persistence (`~/.config/inaki/exchange_config.yaml`) |
| `adapters/outbound/tools/exchange_calendar/calendar_env.py` | `.env` loading, mailbox map resolution |
| `adapters/outbound/tools/exchange_calendar/reader.py` | Read operations (read, search) |
| `adapters/outbound/tools/exchange_calendar/writer.py` | Write operations (create, update, delete) |
| `core/services/crypto_service.py` | Fernet symmetric encryption for the `password` field |

---

## Configuration

### First time — interactive wizard

```bash
python main.py setup
```

The wizard generates `INAKI_SECRET_KEY` (Fernet key) and writes it to `.env`.
This key is used to encrypt the `password` field in the configuration file.

> **Important:** store `INAKI_SECRET_KEY` in a safe place.
> Without it you won't be able to decrypt the saved credentials.

### Configuration from the chat

Once the key is generated, the user can configure Exchange directly in the conversation:

```
you > configure Exchange: user domain\alberto, password ****, server mail.company.com
```

The LLM invokes `operation=configure` and the credentials are persisted in `~/.config/inaki/exchange_config.yaml`.

### Configuration file

Location: `~/.config/inaki/exchange_config.yaml`

```yaml
# Inaki — Exchange Calendar configuration
# The password field is encrypted. Do not edit it manually.

username: dominio\alberto
password: "enc:gAAAAABh..."   # encrypted with Fernet
mail: alberto@empresa.com
ews_url: https://mail.empresa.com/EWS/Exchange.asmx
timezone: Europe/Madrid

calendars:
  - aliases: [juan, juancho]
    email: juan@empresa.com
```

Only `password` is encrypted. Everything else is plain text and auditable.

### Fallback to environment variables

If `~/.config/inaki/exchange_config.yaml` does not exist, the engine reads environment variables as a fallback (useful for development):

| Variable | Description |
|----------|-------------|
| `EXCHANGE_USERNAME` | Login user (domain\user or UPN) |
| `EXCHANGE_PASSWORD` | Password |
| `EXCHANGE_MAIL` | Primary SMTP address of the mailbox |
| `EXCHANGE_EWS_URL` | EWS endpoint URL (optional if autodiscover is active) |
| `EXCHANGE_TIMEZONE` | IANA timezone (default: `UTC`) |
| `EXCHANGE_CALENDAR_MAILBOX_MAP` | Alias→email map (see *Calendar resolution* section) |

Priority: **config store > environment variables**.

### Docker

```dockerfile
# Mount the user's configuration directory as a volume
-v ~/.config/inaki:/root/.config/inaki

# Pass the encryption key via env
--env INAKI_SECRET_KEY=<your_key>
```

With this configuration the `exchange_config.yaml` file persists across container restarts and the encryption key is injected securely without including it in the image.

---

## Operations

### `configure` — save credentials

Persists credentials in `~/.config/inaki/exchange_config.yaml`.
Merges with existing configuration: only overwrites the fields that are provided.

**Required parameters:** `username`, `password`, `mail`
**Optional parameters:** `ews_url`, `timezone`

```json
{
  "operation": "configure",
  "username": "dominio\\alberto",
  "password": "mi_contraseña",
  "mail": "alberto@empresa.com",
  "ews_url": "https://mail.empresa.com/EWS/Exchange.asmx",
  "timezone": "Europe/Madrid"
}
```

Response:
```json
{
  "success": true,
  "message": "Exchange configuration saved successfully.",
  "configured_fields": ["username", "password", "mail", "ews_url", "timezone"]
}
```

---

### `show_config` — view current configuration

Shows the active configuration with the `password` field masked (`***`).

```json
{ "operation": "show_config" }
```

Response (configured):
```json
{
  "success": true,
  "configured": true,
  "config": {
    "username": "dominio\\alberto",
    "password": "***",
    "mail": "alberto@empresa.com",
    "ews_url": "https://mail.empresa.com/EWS/Exchange.asmx",
    "timezone": "Europe/Madrid"
  }
}
```

Response (not configured):
```json
{
  "success": true,
  "configured": false,
  "message": "No Exchange configuration found. Use operation=configure to set credentials."
}
```

---

### `resolve` — resolve name to email

Resolves a name or alias to the exact Exchange email before operating on their calendar.

```json
{
  "operation": "resolve",
  "calendar": "juan"
}
```

Response:
```json
{
  "success": true,
  "email": "juan@empresa.com",
  "display": "juan (juan@empresa.com)",
  "message": "Resolved calendar (juan@empresa.com). Follow up with operation=read..."
}
```

---

### `read` — read events for a period

```json
{
  "operation": "read",
  "calendar": "alberto@empresa.com",
  "start_date": "2026-04-10T00:00:00+02:00",
  "end_date": "2026-04-10T23:59:59+02:00"
}
```

If `calendar` is omitted, the user's own mailbox is used. If dates are omitted, the engine applies a default window.

---

### `search` — search events by subject

```json
{
  "operation": "search",
  "calendar": "alberto@empresa.com",
  "subject": "reunión equipo",
  "start_date": "2026-04-01T00:00:00+02:00",
  "end_date": "2026-04-30T23:59:59+02:00"
}
```

---

### `create` — create event

**Required parameters:** `subject`, `start_date`, `end_date`

```json
{
  "operation": "create",
  "subject": "Reunión de equipo",
  "start_date": "2026-04-15T10:00:00+02:00",
  "end_date": "2026-04-15T11:00:00+02:00",
  "body": "Revisión del sprint.",
  "location": "Sala Madrid",
  "attendees": ["juan@empresa.com", "maria@empresa.com"]
}
```

---

### `update` — update event

Requires `item_id` and `changekey` from a previous `read` or `search` operation.

```json
{
  "operation": "update",
  "item_id": "AAMkADFh...",
  "changekey": "FwAAABYA...",
  "subject": "Reunión de equipo (reprogramada)",
  "start_date": "2026-04-15T11:00:00+02:00",
  "end_date": "2026-04-15T12:00:00+02:00"
}
```

---

### `delete` — delete event

Requires `item_id` and `changekey` from a previous `read` or `search` operation.

```json
{
  "operation": "delete",
  "item_id": "AAMkADFh...",
  "changekey": "FwAAABYA..."
}
```

---

## Calendar Resolution

The tool supports an alias map to resolve names to emails without the LLM needing to know the exact address.

### Via config store (recommended)

Edit `~/.config/inaki/exchange_config.yaml` directly:

```yaml
calendars:
  - aliases: [juan, juancho]
    email: juan@empresa.com
  - aliases: [maria, mary]
    email: maria@empresa.com
```

### Via environment variable (fallback)

```
EXCHANGE_CALENDAR_MAILBOX_MAP=juan|juancho:juan@empresa.com,maria|mary:maria@empresa.com
```

Format per entry: `alias1|alias2|...:email@domain.com`, separated by commas.

### Resolution algorithm (priority)

1. Exact match (alias or email)
2. Single result with matching prefix
3. Single result containing the search string
4. Error with candidates and known list

---

## Security Model

| Element | Where it lives | Encrypted |
|---------|---------------|-----------|
| `INAKI_SECRET_KEY` | `.env` | No (it's the master key) |
| Exchange `password` | `~/.config/inaki/exchange_config.yaml` | Yes (Fernet AES-128-CBC) |
| Other credentials | `~/.config/inaki/exchange_config.yaml` | No (readable plain text) |

The `enc:` prefix in the `password` field value indicates it is encrypted.
If you manually edit the file and remove that prefix, the system will assume the value is plain text and will re-encrypt it on the next save.

---

## Engine Startup Flow

```
ExchangeCalendarEngine.__init__()
  ├── CryptoService()
  │     └── Reads INAKI_SECRET_KEY from .env
  │           └── If missing → generates, writes to .env, logs WARNING
  ├── ExchangeConfigStore(crypto)
  │     └── path: ~/.config/inaki/exchange_config.yaml
  └── _build_config()
        ├── config_store.load()  → decrypts password
        └── fallback to env vars for missing fields
```

The exchangelib `Account` objects are cached by email in the engine instance.
When `operation=configure` is called, the cache is automatically invalidated to force reconnection with the new credentials.
