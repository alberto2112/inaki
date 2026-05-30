# Face Recognition — Technical Guide

Pipeline for processing photos sent via Telegram: face detection, matching against the registry, scene description, and visual annotation.

## General Architecture

```
Telegram photo
     │
     ▼
_handle_photo_message (TelegramBot)
     │  album guard (media_group_id → drop)
     │  auth check
     │
     ▼
extract_photo_payload → bytes JPEG (highest resolution)
     │
     ▼
record_photo_message → history_id (row in history.db)
     │
     ▼
ProcessPhotoUseCase.execute(image_bytes, history_id, ...)
     │
     ├── IVisionPort.detect_and_embed → list[FaceDetection]
     │        InsightFaceVisionAdapter (lazy-load)
     │
     ├── IFaceRegistryPort.search_nearest → FaceMatch per face
     │        SqliteFaceRegistryAdapter (faces.db / sqlite-vec)
     │
     ├── ISceneDescriberPort.describe → scene text
     │        AnthropicSceneDescriberAdapter | OpenAI | Groq
     │
     ├── IPhotoAnnotatorPort.annotate → annotated PNG/JPEG bytes
     │        PillowPhotoAnnotator
     │
     └── IMessageFaceMetadataPort.save → message_face_metadata
              SqliteMessageFaceMetadataRepo (history.db)
     │
     ▼
ProcessPhotoResult(text_context, annotated_image, should_skip_run_agent)
     │
     ▼
RunAgentUseCase.execute(text_context)   ← normal LLM pipeline
```

## Databases

| DB | Path | Contents |
|----|------|----------|
| `faces.db` | `~/.inaki/data/faces.db` | Persons + embeddings (sqlite-vec `FLOAT[512]`) |
| `history.db` | `~/.inaki/data/history.db` | History + `message_face_metadata` side-table |

The two DBs are independent: deleting `faces.db` removes only the face registry; the message history remains intact.

### faces.db Schema

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
    categoria   VARCHAR,   -- NULL=normal, 'ignorada'=permanent skip
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

### message_face_metadata Schema (in history.db)

```sql
CREATE TABLE message_face_metadata (
    id          INTEGER PRIMARY KEY,
    history_id  INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    data        TEXT NOT NULL   -- JSON: list[FaceRecord]
);
```

`data` serializes the list of `FaceRecord` with `face_ref`, `person_id`, `match_score`, `bbox`.

**face_ref format**: `"{history_id}#{face_idx}"` — globally unique key to refer to a specific face in a message. Tools receive it as an argument for subsequent enrollment operations.

## InsightFace Lazy-Load

The model (~400 MB) **is not loaded at daemon startup**. It is loaded the first time a photo is received. Implemented with a lazy singleton in the adapter's `_get_app()`:

```python
def _get_app(self) -> FaceAnalysis:
    if self._app is None:
        self._app = FaceAnalysis(name=self._model_name, providers=["CPUExecutionProvider"])
        self._app.prepare(ctx_id=0, det_size=(640, 640))
    return self._app
```

The first analysis takes ~5-10 seconds on Pi 5. Subsequent ones are instantaneous.

## Embedding Dimension Validation

On startup, `SqliteFaceRegistryAdapter` verifies that `schema_meta.embedding_dim` matches the actual model dimension. If they don't match, it raises `EmbeddingDimensionMismatchError` with a descriptive message.

**If you change `faces.model`**: the dimension may change. Procedure:

```bash
rm ~/.inaki/data/faces.db
# restart the daemon — it is recreated automatically
# re-enroll all persons via register_face / add_photo_to_person
```

## Person Categories

The `categoria VARCHAR` field in `persons` is extensible without migrations:

| Value | Meaning |
|-------|---------|
| `NULL` | Normal person — appears in matching |
| `'ignorada'` | Registered via `skip_face` — silently filtered out in process_photo |

Future values are possible with a simple `ALTER TABLE` (or just by writing the new value).

## Available Face Tools

All tools are registered on the agent when `photos.enabled=true`.

| Tool | Description |
|------|-------------|
| `register_face` | Registers a new face as a person (face_ref → person_id) |
| `add_photo_to_person` | Adds a photo embedding to an existing person |
| `update_person_metadata` | Updates a person's name/alias/notes |
| `list_known_persons` | Lists all persons in the registry |
| `forget_person` | Deletes a person and all their embeddings |
| `skip_face` | Marks a face to be permanently ignored |
| `merge_persons` | Merges two persons into one (deduplicates) |
| `find_duplicate_persons` | Detects pairs of persons with similar embeddings |

## Nightly Deduplication Job

If `photos.dedup.enabled=true`, the built-in task `face_dedup_nightly` (id=2) is seeded in the scheduler. By default it runs at 3am (`"0 3 * * *"`). The job sends the first agent with photos enabled the task:

> "Run the find_duplicate_persons tool and report any duplicate person pairs you find, if any."

Configurable via `photos.dedup.schedule` and `photos.dedup.similarity_threshold`.

## Minimal Configuration

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

See the full configuration reference in [`docs/configuracion.md`](configuracion.md).

## Bootstrap from Scratch

```bash
# 1. Stop the daemon
systemctl --user stop inaki

# 2. Add the photos: block in global.yaml (or secrets.yaml for the api_key)
# 3. Restart — faces.db is created automatically on first use
systemctl --user start inaki

# 4. Send a photo via Telegram
# 5. The agent will detect the face and offer to register it (in private chat)
# 6. Reply with the name to enroll: register_face
```
