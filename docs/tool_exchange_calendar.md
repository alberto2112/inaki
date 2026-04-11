# Tool: exchange_calendar

Integración con Microsoft Exchange para gestión de calendario vía EWS (Exchange Web Services).
Permite leer, buscar, crear, actualizar y eliminar eventos del calendario de Outlook desde el agente.

---

## Archivos relevantes

| Archivo | Rol |
|---------|-----|
| `adapters/outbound/tools/exchange_calendar_tool.py` | Facade: validación, schema, routing hacia el engine |
| `adapters/outbound/tools/exchange_calendar/engine.py` | Lógica central: cuentas, dispatch de operaciones |
| `adapters/outbound/tools/exchange_calendar/config_store.py` | Persistencia de credenciales (`~/.config/inaki/exchange_config.yaml`) |
| `adapters/outbound/tools/exchange_calendar/calendar_env.py` | Carga de `.env`, resolución del mailbox map |
| `adapters/outbound/tools/exchange_calendar/reader.py` | Operaciones de lectura (read, search) |
| `adapters/outbound/tools/exchange_calendar/writer.py` | Operaciones de escritura (create, update, delete) |
| `core/services/crypto_service.py` | Cifrado simétrico Fernet para el campo `password` |

---

## Configuración

### Primera vez — wizard interactivo

```bash
python main.py setup
```

El wizard genera `INAKI_SECRET_KEY` (clave Fernet) y la escribe en `.env`.
Esta clave se usa para cifrar el campo `password` en el archivo de configuración.

> **Importante:** guarda `INAKI_SECRET_KEY` en un lugar seguro.
> Sin ella no podrás descifrar las credenciales guardadas.

### Configuración desde el chat

Una vez generada la clave, el usuario puede configurar Exchange directamente en la conversación:

```
tú > configurá Exchange: usuario dominio\alberto, contraseña ****, servidor mail.empresa.com
```

El LLM invoca `operation=configure` y las credenciales se persisten en `~/.config/inaki/exchange_config.yaml`.

### Archivo de configuración

Ubicación: `~/.config/inaki/exchange_config.yaml`

```yaml
# Iñaki — Exchange Calendar configuration
# El campo password está cifrado. No lo edites manualmente.

username: dominio\alberto
password: "enc:gAAAAABh..."   # cifrado con Fernet
mail: alberto@empresa.com
ews_url: https://mail.empresa.com/EWS/Exchange.asmx
timezone: Europe/Madrid

calendars:
  - aliases: [juan, juancho]
    email: juan@empresa.com
```

Solo `password` está cifrado. El resto es texto plano y auditable.

### Fallback a variables de entorno

Si no existe `~/.config/inaki/exchange_config.yaml`, el engine lee las variables de entorno como fallback (útil para desarrollo):

| Variable | Descripción |
|----------|-------------|
| `EXCHANGE_USERNAME` | Usuario de login (dominio\usuario o UPN) |
| `EXCHANGE_PASSWORD` | Contraseña |
| `EXCHANGE_MAIL` | Dirección SMTP primaria del buzón |
| `EXCHANGE_EWS_URL` | URL del endpoint EWS (opcional si autodiscover está activo) |
| `EXCHANGE_TIMEZONE` | Zona horaria IANA (default: `UTC`) |
| `EXCHANGE_CALENDAR_MAILBOX_MAP` | Mapa alias→email (ver sección *Resolución de calendarios*) |

Prioridad: **config store > variables de entorno**.

### Docker

```dockerfile
# Montar el directorio de configuración del usuario como volumen
-v ~/.config/inaki:/root/.config/inaki

# Pasar la clave de cifrado via env
--env INAKI_SECRET_KEY=<tu_clave>
```

Con esta configuración el archivo `exchange_config.yaml` persiste entre reinicios del contenedor y la clave de cifrado se inyecta de forma segura sin incluirla en la imagen.

---

## Operaciones

### `configure` — guardar credenciales

Persiste las credenciales en `~/.config/inaki/exchange_config.yaml`.
Hace merge con la configuración existente: solo sobrescribe los campos que se proveen.

**Parámetros requeridos:** `username`, `password`, `mail`
**Parámetros opcionales:** `ews_url`, `timezone`

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

Respuesta:
```json
{
  "success": true,
  "message": "Exchange configuration saved successfully.",
  "configured_fields": ["username", "password", "mail", "ews_url", "timezone"]
}
```

---

### `show_config` — ver configuración actual

Muestra la configuración activa con el campo `password` enmascarado (`***`).

```json
{ "operation": "show_config" }
```

Respuesta (configurado):
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

Respuesta (sin configurar):
```json
{
  "success": true,
  "configured": false,
  "message": "No Exchange configuration found. Use operation=configure to set credentials."
}
```

---

### `resolve` — resolver nombre a email

Resuelve un nombre o alias al email exacto de Exchange antes de operar sobre su calendario.

```json
{
  "operation": "resolve",
  "calendar": "juan"
}
```

Respuesta:
```json
{
  "success": true,
  "email": "juan@empresa.com",
  "display": "juan (juan@empresa.com)",
  "message": "Resolved calendar (juan@empresa.com). Follow up with operation=read..."
}
```

---

### `read` — leer eventos de un período

```json
{
  "operation": "read",
  "calendar": "alberto@empresa.com",
  "start_date": "2026-04-10T00:00:00+02:00",
  "end_date": "2026-04-10T23:59:59+02:00"
}
```

Si `calendar` se omite, usa el buzón propio. Si se omiten las fechas, el engine aplica una ventana por defecto.

---

### `search` — buscar eventos por asunto

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

### `create` — crear evento

**Parámetros requeridos:** `subject`, `start_date`, `end_date`

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

### `update` — actualizar evento

Requiere `item_id` y `changekey` de una operación `read` o `search` previa.

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

### `delete` — eliminar evento

Requiere `item_id` y `changekey` de una operación `read` o `search` previa.

```json
{
  "operation": "delete",
  "item_id": "AAMkADFh...",
  "changekey": "FwAAABYA..."
}
```

---

## Resolución de calendarios

La tool soporta un mapa de alias para resolver nombres a emails sin que el LLM necesite conocer la dirección exacta.

### Vía config store (recomendado)

Editar directamente `~/.config/inaki/exchange_config.yaml`:

```yaml
calendars:
  - aliases: [juan, juancho]
    email: juan@empresa.com
  - aliases: [maria, mary]
    email: maria@empresa.com
```

### Vía variable de entorno (fallback)

```
EXCHANGE_CALENDAR_MAILBOX_MAP=juan|juancho:juan@empresa.com,maria|mary:maria@empresa.com
```

Formato por entrada: `alias1|alias2|...:email@dominio.com`, separadas por comas.

### Algoritmo de resolución (prioridad)

1. Coincidencia exacta (alias o email)
2. Un único resultado con prefijo coincidente
3. Un único resultado que contiene la cadena buscada
4. Error con candidatos y lista conocida

---

## Modelo de seguridad

| Elemento | Dónde vive | Cifrado |
|----------|-----------|---------|
| `INAKI_SECRET_KEY` | `.env` | No (es la clave maestra) |
| `password` de Exchange | `~/.config/inaki/exchange_config.yaml` | Sí (Fernet AES-128-CBC) |
| Resto de credenciales | `~/.config/inaki/exchange_config.yaml` | No (texto plano legible) |

El prefijo `enc:` en el valor del campo `password` indica que está cifrado.
Si editás el archivo manualmente y borrás ese prefijo, el sistema asumirá que el valor es texto plano y lo re-cifrará al próximo guardado.

---

## Flujo de arranque del engine

```
ExchangeCalendarEngine.__init__()
  ├── CryptoService()
  │     └── Lee INAKI_SECRET_KEY de .env
  │           └── Si no existe → genera, escribe en .env, loggea WARNING
  ├── ExchangeConfigStore(crypto)
  │     └── path: ~/.config/inaki/exchange_config.yaml
  └── _build_config()
        ├── config_store.load()  → descifra password
        └── fallback a env vars para campos ausentes
```

Los objetos `Account` de exchangelib se cachean por email en la instancia del engine.
Al llamar `operation=configure`, el cache se invalida automáticamente para forzar reconexión con las nuevas credenciales.
