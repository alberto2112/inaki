# Reconocimiento facial — Guía técnica

Pipeline de procesamiento de fotos enviadas por Telegram: detección de caras, matching contra el registro, descripción de escena y anotación visual.

## Arquitectura general

```
Telegram foto
     │
     ▼
_handle_photo_message (TelegramBot)
     │  album guard (media_group_id → drop)
     │  auth check
     │
     ▼
extract_photo_payload → bytes JPEG (mayor resolución)
     │
     ▼
record_photo_message → history_id (fila en history.db)
     │
     ▼
ProcessPhotoUseCase.execute(image_bytes, history_id, ...)
     │
     ├── IVisionPort.detect_and_embed → list[FaceDetection]
     │        InsightFaceVisionAdapter (lazy-load)
     │
     ├── IFaceRegistryPort.search_nearest → FaceMatch per cara
     │        SqliteFaceRegistryAdapter (faces.db / sqlite-vec)
     │
     ├── ISceneDescriberPort.describe → texto de escena
     │        AnthropicSceneDescriberAdapter | OpenAI | Groq
     │
     ├── IPhotoAnnotatorPort.annotate → bytes PNG/JPEG anotado
     │        PillowPhotoAnnotator
     │
     └── IMessageFaceMetadataPort.save → message_face_metadata
              SqliteMessageFaceMetadataRepo (history.db)
     │
     ▼
ProcessPhotoResult(text_context, annotated_image, should_skip_run_agent)
     │
     ▼
RunAgentUseCase.execute(text_context)   ← pipeline LLM normal
```

## Bases de datos

| DB | Ruta | Contenido |
|----|------|-----------|
| `faces.db` | `~/.inaki/data/faces.db` | Personas + embeddings (sqlite-vec `FLOAT[512]`) |
| `history.db` | `~/.inaki/data/history.db` | Historial + `message_face_metadata` side-table |

Las dos DBs son independientes: borrar `faces.db` elimina solo el registro facial; el historial de mensajes permanece intacto.

### Esquema faces.db

```sql
CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- key = 'embedding_dim', value = '512'

CREATE TABLE persons (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    alias       TEXT,
    notes       TEXT,
    categoria   VARCHAR,   -- NULL=normal, 'ignorada'=skip permanente
    created_at  DATETIME,
    updated_at  DATETIME
);

CREATE VIRTUAL TABLE face_embeddings USING vec0(
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    embedding   FLOAT[512],
    created_at  TEXT
);
```

### Esquema message_face_metadata (en history.db)

```sql
CREATE TABLE message_face_metadata (
    id          INTEGER PRIMARY KEY,
    history_id  INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    data        TEXT NOT NULL   -- JSON: list[FaceRecord]
);
```

`data` serializa la lista de `FaceRecord` con `face_ref`, `person_id`, `match_score`, `bbox`.

**face_ref format**: `"{history_id}#{face_idx}"` — clave globalmente única para referirse a una cara específica en un mensaje. Las tools la reciben como argumento para operaciones de enrollment posterior.

## Lazy-load de InsightFace

El modelo (~400 MB) **no se carga al arrancar el daemon**. Se carga la primera vez que se recibe una foto. Implementado con un singleton perezoso en `_get_app()` del adapter:

```python
def _get_app(self) -> FaceAnalysis:
    if self._app is None:
        self._app = FaceAnalysis(name=self._model_name, providers=["CPUExecutionProvider"])
        self._app.prepare(ctx_id=0, det_size=(640, 640))
    return self._app
```

El primer análisis tarda ~5-10 segundos en Pi 5. Los siguientes son instantáneos.

## Validación de dimensión de embedding

Al iniciar, `SqliteFaceRegistryAdapter` verifica que `schema_meta.embedding_dim` coincide con la dimensión real del modelo. Si no coinciden, lanza `EmbeddingDimensionMismatchError` con un mensaje descriptivo.

**Si cambiás `faces.model`**: la dimensión puede cambiar. Procedimiento:

```bash
rm ~/.inaki/data/faces.db
# reiniciar el daemon — se recrea automáticamente
# re-enrolar todas las personas via register_face / add_photo_to_person
```

## Categorías de personas

El campo `categoria VARCHAR` en `persons` es extensible sin migraciones:

| Valor | Significado |
|-------|-------------|
| `NULL` | Persona normal — aparece en matching |
| `'ignorada'` | Registrada via `skip_face` — filtrada silenciosamente en process_photo |

Futuros valores son posibles con un `ALTER TABLE` simple (o simplemente escribiendo el nuevo valor).

## Face tools disponibles

Todas las tools se registran en el agente cuando `photos.enabled=true`.

| Tool | Descripción |
|------|-------------|
| `register_face` | Registra una cara nueva como persona (face_ref → person_id) |
| `add_photo_to_person` | Añade un embedding de foto a una persona existente |
| `update_person_metadata` | Actualiza nombre/alias/notas de una persona |
| `list_known_persons` | Lista todas las personas en el registro |
| `forget_person` | Elimina una persona y todos sus embeddings |
| `skip_face` | Marca una cara para ser ignorada permanentemente |
| `merge_persons` | Fusiona dos personas en una (deduplica) |
| `find_duplicate_persons` | Detecta pares de personas con embeddings similares |

## Job de deduplicación nocturna

Si `photos.dedup.enabled=true`, se siembra la tarea builtin `face_dedup_nightly` (id=2) en el scheduler. Por defecto corre a las 3am (`"0 3 * * *"`). El job envía al primer agente con photos habilitado la tarea:

> "Ejecutá la herramienta find_duplicate_persons y reportá los pares de personas duplicadas que encontrés, si hay alguno."

Configurable via `photos.dedup.schedule` y `photos.dedup.similarity_threshold`.

## Configuración mínima

```yaml
# ~/.inaki/config/global.yaml
photos:
  enabled: true
  scene:
    provider: anthropic
    model: claude-haiku-4-5-20251001

# ~/.inaki/config/global.secrets.yaml
photos:
  scene:
    api_key: "sk-ant-..."
```

Ver la referencia completa de configuración en [`docs/configuracion.md`](configuracion.md).

## Bootstrap desde cero

```bash
# 1. Detener el daemon
systemctl --user stop inaki

# 2. Agregar bloque photos: en global.yaml (o secrets.yaml para api_key)
# 3. Reiniciar — faces.db se crea automáticamente al primer uso
systemctl --user start inaki

# 4. Enviar una foto por Telegram
# 5. El agente detectará la cara y ofrecerá registrarla (en chat privado)
# 6. Responder con el nombre para enrollar:  register_face
```
