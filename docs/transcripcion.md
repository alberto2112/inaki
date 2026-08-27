# `transcription` — transcripción de voz (Telegram)

Habilita la transcripción de mensajes de voz, audio y `video_note` en Telegram.
Se define en `config/global.yaml` (sobreescribible por agente) y se activa con
`channels.telegram.voice_enabled: true` (default).

```yaml
transcription:
  provider: "groq"                     # referencia a providers.groq
  model: "whisper-large-v3-turbo"
  language: "es"                       # ISO-639-1; null = autodetectar
  timeout_seconds: 60
  max_audio_mb: 25                     # audios más grandes se rechazan sin llamar al provider
```

## Providers disponibles

Auto-descubiertos desde `adapters/outbound/transcription/` vía la constante
`PROVIDER_NAME` a nivel de módulo — sin registro manual.

| `provider` | `base_url` por defecto | `model` típico |
|------------|------------------------|----------------|
| `groq`     | `https://api.groq.com/openai/v1` | `whisper-large-v3-turbo` |
| `openai`   | `https://api.openai.com/v1`      | `whisper-1` |

Ambos hablan el mismo dialecto `/audio/transcriptions` de OpenAI (Groq clonó su
API), así que un único adapter base (`BaseTranscriptionProvider`) sirve para los
dos — cada provider concreto solo declara su `base_url` por defecto y su
etiqueta. Para usar OpenAI:

```yaml
transcription:
  provider: "openai"                   # referencia a providers.openai
  model: "whisper-1"
```

> **El registry de transcripción es independiente de los de LLM y embedding.** Que
> un provider esté disponible como LLM (p. ej. `openrouter`, `deepseek`) NO lo
> hace disponible para transcripción — acá solo viven los servicios que exponen
> de verdad la API de audio (hoy: `groq`, `openai`). OpenRouter, por ejemplo,
> solo rutea `/chat/completions` y no tiene endpoint de transcripción.

Las credenciales (`api_key`, `base_url`) NO van en este bloque — se resuelven
desde `providers.<provider>` en el registry (p. ej. `providers.groq`).

## Feature flag en el agente

```yaml
channels:
  telegram:
    voice_enabled: true   # default — acepta voice/audio/video_note
    # voice_enabled: false — sin transcripción ni turno; el file_id y un bloque
    # de attachment `@audio` se persisten igual (persistencia simétrica)
```

## Flujo del handler de voz

También aplica a documentos con mime `audio/*` — un mp3 adjuntado "como fichero"
entra por acá.

1. Usuario autorizado (`allowed_user_ids`) — si no, se descarta en silencio.
2. `file_id` persistido en `telegram_files.db` SIEMPRE (antes de cualquier chequeo de feature).
3. `voice_enabled: true` — si no, se persiste el bloque `@audio` en historial
   (sin turno) y se corta.
4. Tamaño ≤ `max_audio_mb` — si no, bloque `@audio` + reacción 👎 + respuesta.
5. Reacción 👀, transcripción → turno cuyo mensaje de usuario es el bloque de
   attachment `@audio ... at <local_path>` + `@transcription: <texto>` (de ahí en
   adelante, el mismo pipeline que un mensaje de texto). Una transcripción fallida
   o vacía también deja el bloque `@audio`.

## Errores de arranque comunes

- Agente con `voice_enabled: true` y sin bloque `transcription:` resuelto → falla
  durante el bootstrap con un error claro pidiendo agregar `transcription:` o
  poner `voice_enabled: false`.
- Falta `providers.<provider>.api_key` para el provider referenciado por
  `transcription.provider` → `ConfigError` al arrancar (fail-fast, antes de
  instanciar adapters).

> ⚠ **Privacidad:** el audio se manda al provider externo configurado en
> `transcription.provider` (Groq u OpenAI). Para contenido sensible poné
> `voice_enabled: false` en ese agente, o esperá a que haya un provider local. El
> fichero de audio SÍ queda cacheado en disco en
> `<workspace>/telegram/<file_unique_id>.<ext>` (para que el LLM pueda operar
> sobre él por su path local), y el texto transcrito queda en `chat_history` y
> puede alimentar la memoria.
