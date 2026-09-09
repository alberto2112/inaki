"""Percepción — fotos, caras, escena y voz, independientes del canal que las recibe.

- ``domain/face``: personas, detecciones y embeddings faciales.
- ``ports/``: ``IVisionProvider`` (detección), ``IFaceRegistry`` (quién es quién),
  ``ISceneDescriber`` (qué hay en la foto), ``ITranscriptionProvider`` (voz → texto),
  ``IMessageFaceMetadata`` (caras por mensaje del historial).
- ``use_cases/``: ``ProcessPhotoUseCase`` y ``TranscribeAudioUseCase`` — lo que un
  canal invoca cuando recibe una foto o un audio, sin saber cómo se procesa.
- ``adapters/``: InsightFace, registro SQLite de caras, describers de escena por
  vendor, anotador Pillow, transcripción OpenAI-compatible.
- ``tools/``: las tools de caras que el agente expone al LLM.

Harness-global (``photos``): el modelo de visión se carga UNA vez por proceso.
"""
