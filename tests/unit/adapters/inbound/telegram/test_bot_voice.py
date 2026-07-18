"""Tests del handler de voz de TelegramBot.

Cubre:
- Usuario no autorizado → drop silencioso, sin invocar provider ni pipeline.
- voice_enabled=False → persiste el marcador @audio, sin transcribir ni turno.
- Audio demasiado grande → marcador @audio + reply de error + reacción 👎, SIN provider.
- Happy path: reacción 👀, transcripción, turno con bloque @audio + @transcription.
- TranscriptionError del provider → marcador + reply de error + 👎, pipeline no corre.
- Document con mime audio/* → _handle_silent_media delega al pipeline de voz.
- Mensaje sin voice/audio/video_note (defensa) → no-op.

Todos los tests instancian TelegramBot mockeando `Application` (mismo patrón
que test_bot_clear.py) y mockean el container con `mock_transcription`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain.errors import TranscriptionError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mk_agent_cfg(
    *,
    voice_enabled: bool = True,
    allowed_user_ids: list[int] | None = None,
    reactions: bool = True,
    max_audio_mb: int = 25,
    language: str | None = None,
) -> MagicMock:
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.name = "Inaki"
    cfg.description = "Asistente"
    tg = {
        "token": "dummy-token",
        "allowed_user_ids": allowed_user_ids or [],
        "reactions": reactions,
        "voice_enabled": voice_enabled,
    }
    cfg.telegram = tg
    # Workspace real bajo /tmp: _save_bytes_to_workspace escribe los bytes acá.
    cfg.workspace_path = "/tmp/inaki-test-ws-voice"
    # Transcription config embebida: max_audio_mb + language.
    cfg.transcription = MagicMock()
    cfg.transcription.max_audio_mb = max_audio_mb
    cfg.transcription.language = language
    return cfg


@pytest.fixture
def agent_cfg() -> MagicMock:
    return _mk_agent_cfg(voice_enabled=True, allowed_user_ids=[12345], reactions=True)


@pytest.fixture
def agent_cfg_voice_off() -> MagicMock:
    return _mk_agent_cfg(voice_enabled=False, allowed_user_ids=[12345])


@pytest.fixture
def mock_container(mock_transcription) -> MagicMock:
    container = MagicMock()
    container.transcription = mock_transcription
    container.run_agent = MagicMock()
    container.run_agent.execute = AsyncMock(return_value="Respuesta del agente")
    container.run_agent.record_user_message = AsyncMock(return_value=None)
    # Sin repo ni downloader: la persistencia de file_id es no-op y los
    # marcadores degradan a "pending" cuando no hay bytes en memoria.
    container.telegram_file_repo = None
    container.telegram_file_downloader = None
    # scope_registry para in-flight-message-injection — try_mark_busy=True
    # significa "scope libre", el camino normal corre execute() como antes.
    container.scope_registry = MagicMock()
    container.scope_registry.try_mark_busy = AsyncMock(return_value=True)
    container.scope_registry.mark_idle = AsyncMock(return_value=None)
    return container


def _build_bot(agent_cfg, mock_container):
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(settings=agent_cfg, ports=mock_container)


def _mk_update(
    *,
    user_id: int = 12345,
    chat_id: int = 99,
    voice=None,
    audio=None,
    video_note=None,
    document=None,
):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    msg = MagicMock()
    # Stubs explícitos: MagicMock auto-genera atributos TRUTHY y
    # _extract_file_metadata clasificaría el mensaje como foto.
    msg.photo = []
    msg.voice = voice
    msg.audio = audio
    msg.video = None
    msg.video_note = video_note
    msg.document = document
    msg.media_group_id = None
    msg.caption = None
    msg.text = None
    msg.reply_text = AsyncMock()
    msg.set_reaction = AsyncMock()
    update.message = msg
    return update


def _mk_voice(bytes_result: bytes = b"audio-data", file_size: int = 1024):
    voice = MagicMock()
    voice.file_id = "AUD-1"
    voice.file_unique_id = "AUD-uniq"
    voice.file_name = None
    voice.mime_type = None
    voice.file_size = file_size
    f = MagicMock()
    f.download_as_bytearray = AsyncMock(return_value=bytearray(bytes_result))
    voice.get_file = AsyncMock(return_value=f)
    return voice


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_user_no_autorizado_drop_silencioso(agent_cfg, mock_container) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(user_id=999, voice=_mk_voice())
    context = MagicMock()

    await bot._handle_voice_message(update, context)

    mock_container.transcription.transcribe.assert_not_called()
    mock_container.run_agent.execute.assert_not_called()
    update.message.reply_text.assert_not_called()


async def test_voice_enabled_false_persiste_marcador_sin_transcribir(
    agent_cfg_voice_off, mock_container
) -> None:
    """Con voz deshabilitada NO se transcribe ni corre turno, pero el bloque
    @audio queda en el historial (persistencia simétrica) — sin downloader el
    marcador degrada a pending."""
    bot = _build_bot(agent_cfg_voice_off, mock_container)
    update = _mk_update(voice=_mk_voice())
    context = MagicMock()

    await bot._handle_voice_message(update, context)

    mock_container.transcription.transcribe.assert_not_called()
    mock_container.run_agent.execute.assert_not_called()
    mock_container.run_agent.record_user_message.assert_awaited_once()
    marker = mock_container.run_agent.record_user_message.await_args.args[0]
    assert marker.startswith("@audio")
    assert "pending (id: AUD-uniq)" in marker


async def test_happy_path_transcribe_y_pipeline(agent_cfg, mock_container) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    voice = _mk_voice(bytes_result=b"audio-bytes", file_size=500)
    update = _mk_update(voice=voice)
    context = MagicMock()

    mock_container.transcription.transcribe.return_value = "hola mundo"

    await bot._handle_voice_message(update, context)

    # Transcribe invocado con los bytes correctos.
    mock_container.transcription.transcribe.assert_awaited_once()
    call = mock_container.transcription.transcribe.await_args
    assert call.kwargs.get("audio", call.args[0] if call.args else None) == b"audio-bytes"
    # Turno invocado con el bloque @audio + @transcription (no el texto crudo).
    mock_container.run_agent.execute.assert_awaited_once()
    user_input = mock_container.run_agent.execute.await_args.args[0]
    assert user_input.startswith("@audio")
    assert "AUD-uniq" in user_input  # el path local lleva el file_unique_id
    assert "@transcription: hola mundo" in user_input
    # Reply final enviado.
    update.message.reply_text.assert_awaited()
    # En el happy path solo se reacciona con 👀 al recibir el audio.
    reactions_sent = [c.args[0] for c in update.message.set_reaction.await_args_list]
    assert reactions_sent == ["👀"]


async def test_audio_en_grupo_se_prefija_con_sender(agent_cfg, mock_container) -> None:
    """En grupos, el bloque se inyecta al pipeline con el prefijo
    ``"{sender} (audio):"`` — mismo espíritu que `_format_history_prefix`
    aplica a los broadcasts entrantes."""
    bot = _build_bot(agent_cfg, mock_container)
    voice = _mk_voice(bytes_result=b"audio-bytes", file_size=500)
    update = _mk_update(voice=voice)
    update.message.chat.type = "supergroup"
    update.message.from_user.username = "alberto"
    context = MagicMock()

    mock_container.transcription.transcribe.return_value = "cuánto es 5+5"

    await bot._handle_voice_message(update, context)

    user_input = mock_container.run_agent.execute.await_args.args[0]
    assert user_input.startswith("alberto (audio):\n@audio")
    assert "@transcription: cuánto es 5+5" in user_input


async def test_audio_en_privado_no_se_prefija(agent_cfg, mock_container) -> None:
    """En privado el bloque va crudo — no hay otros remitentes que requieran
    identificar al sender."""
    bot = _build_bot(agent_cfg, mock_container)
    voice = _mk_voice(bytes_result=b"audio-bytes", file_size=500)
    update = _mk_update(voice=voice)
    update.message.chat.type = "private"
    update.message.from_user.username = "alberto"
    context = MagicMock()

    mock_container.transcription.transcribe.return_value = "hola mundo"

    await bot._handle_voice_message(update, context)

    user_input = mock_container.run_agent.execute.await_args.args[0]
    assert user_input.startswith("@audio")
    assert "@transcription: hola mundo" in user_input


async def test_audio_demasiado_grande_no_llama_provider(agent_cfg, mock_container) -> None:
    agent_cfg.transcription.max_audio_mb = 1  # 1 MB
    bot = _build_bot(agent_cfg, mock_container)
    # file_size > 1 MB
    voice = _mk_voice(bytes_result=b"x", file_size=2 * 1024 * 1024)
    update = _mk_update(voice=voice)
    context = MagicMock()

    await bot._handle_voice_message(update, context)

    mock_container.transcription.transcribe.assert_not_called()
    mock_container.run_agent.execute.assert_not_called()
    # El bloque @audio igual queda en el historial (persistencia simétrica).
    mock_container.run_agent.record_user_message.assert_awaited_once()
    marker = mock_container.run_agent.record_user_message.await_args.args[0]
    assert marker.startswith("@audio")
    # Debe haber respondido al usuario con el error.
    update.message.reply_text.assert_awaited()
    # 👎 es la reacción negativa válida en el whitelist de Telegram (❌ no lo era).
    reactions_sent = [c.args[0] for c in update.message.set_reaction.await_args_list]
    assert "👎" in reactions_sent


async def test_provider_raises_transcription_error(agent_cfg, mock_container) -> None:
    mock_container.transcription.transcribe.side_effect = TranscriptionError("formato no soportado")
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(voice=_mk_voice(file_size=100))
    context = MagicMock()

    await bot._handle_voice_message(update, context)

    # Pipeline NO debe correr si la transcripción falla.
    mock_container.run_agent.execute.assert_not_called()
    # Pero el bloque @audio queda en el historial (sin @transcription).
    mock_container.run_agent.record_user_message.assert_awaited_once()
    marker = mock_container.run_agent.record_user_message.await_args.args[0]
    assert marker.startswith("@audio")
    assert "@transcription" not in marker
    # Debe haber replied con el error.
    update.message.reply_text.assert_awaited()
    reactions_sent = [c.args[0] for c in update.message.set_reaction.await_args_list]
    assert "👎" in reactions_sent


async def test_audio_no_presente_noop(agent_cfg, mock_container) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update()  # sin voice/audio/video_note
    context = MagicMock()

    await bot._handle_voice_message(update, context)

    mock_container.transcription.transcribe.assert_not_called()
    mock_container.run_agent.execute.assert_not_called()


async def test_video_note_se_procesa_igual_que_voice(agent_cfg, mock_container) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    vn = _mk_voice(bytes_result=b"vn-bytes", file_size=200)
    update = _mk_update(video_note=vn)
    context = MagicMock()

    mock_container.transcription.transcribe.return_value = "video transcripto"

    await bot._handle_voice_message(update, context)

    mock_container.transcription.transcribe.assert_awaited_once()
    mock_container.run_agent.execute.assert_awaited_once()
    # video_note se clasifica como video → bloque @video.
    user_input = mock_container.run_agent.execute.await_args.args[0]
    assert user_input.startswith("@video")


async def test_audio_file_se_procesa(agent_cfg, mock_container) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    audio = _mk_voice(bytes_result=b"mp3", file_size=300)
    audio.mime_type = "audio/mpeg"
    update = _mk_update(audio=audio)
    context = MagicMock()

    mock_container.transcription.transcribe.return_value = "audio mp3 transcripto"

    await bot._handle_voice_message(update, context)

    mock_container.transcription.transcribe.assert_awaited_once()
    # El mime del audio debe haber llegado al provider como audio/mpeg.
    call = mock_container.transcription.transcribe.await_args
    # mime puede venir como kwarg o arg posicional según la impl — aceptamos ambos.
    mime_value = call.kwargs.get("mime")
    if mime_value is None and len(call.args) >= 2:
        mime_value = call.args[1]
    assert mime_value == "audio/mpeg"


async def test_document_con_mime_audio_rutea_al_pipeline_de_voz(agent_cfg, mock_container) -> None:
    """Un mp3 adjuntado 'como archivo' llega como document con mime audio/* —
    _handle_silent_media debe delegarlo al pipeline de voz, no tratarlo como
    depósito de archivo genérico (bug del 'audio viejo')."""
    bot = _build_bot(agent_cfg, mock_container)
    doc = _mk_voice(bytes_result=b"mp3-bytes", file_size=400)
    doc.mime_type = "audio/mpeg"
    doc.file_name = "nota-de-voz.mp3"
    update = _mk_update(document=doc)
    update.message.chat.type = "private"
    context = MagicMock()

    mock_container.transcription.transcribe.return_value = "contenido del mp3"

    await bot._handle_silent_media(update, context)

    mock_container.transcription.transcribe.assert_awaited_once()
    mock_container.run_agent.execute.assert_awaited_once()
    user_input = mock_container.run_agent.execute.await_args.args[0]
    assert user_input.startswith("@audio nota-de-voz.mp3")
    assert "@transcription: contenido del mp3" in user_input


async def test_reactions_false_no_envia_set_reaction(mock_container) -> None:
    agent_cfg = _mk_agent_cfg(voice_enabled=True, allowed_user_ids=[12345], reactions=False)
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(voice=_mk_voice())
    context = MagicMock()

    mock_container.transcription.transcribe.return_value = "ok"

    await bot._handle_voice_message(update, context)

    update.message.set_reaction.assert_not_called()


def test_bot_registra_handlers_voice_audio_video_note(agent_cfg, mock_container) -> None:
    """Task 3.7: el __init__ del bot engancha un MessageHandler por cada filtro
    de audio (VOICE, AUDIO, VIDEO_NOTE) apuntando a `_handle_voice_message`."""
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(settings=agent_cfg, ports=mock_container)

    # Se registran 3 MessageHandlers de audio (además del de texto y los commands).
    registered = [c.args[0] for c in mock_app.add_handler.call_args_list]
    voice_handler_callbacks = [
        h.callback
        for h in registered
        if hasattr(h, "callback") and h.callback == bot._handle_voice_message
    ]
    assert len(voice_handler_callbacks) == 3, (
        f"Se esperaban 3 handlers apuntando a _handle_voice_message, "
        f"se encontraron {len(voice_handler_callbacks)}"
    )


def test_voice_enabled_false_registra_handlers_para_persistencia(mock_container) -> None:
    """Los handlers de voz se registran SIEMPRE: ``voice_enabled`` controla solo
    si transcribir, NO si persistir el ``file_id`` en ``telegram_files.db``.

    Esto permite que ``download_from_telegram`` recupere audios incluso si la
    transcripción está deshabilitada en la config.
    """
    cfg = _mk_agent_cfg(voice_enabled=False, allowed_user_ids=[12345])
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(settings=cfg, ports=mock_container)

    registered = [c.args[0] for c in mock_app.add_handler.call_args_list]
    voice_handler_callbacks = [
        h.callback
        for h in registered
        if hasattr(h, "callback") and h.callback == bot._handle_voice_message
    ]
    # 3 filtros: VOICE, AUDIO, VIDEO_NOTE — todos apuntan al mismo callback.
    assert len(voice_handler_callbacks) == 3, (
        f"Esperaba 3 handlers de voz registrados, encontrados {len(voice_handler_callbacks)}"
    )


def test_handlers_de_voz_se_registran_antes_que_handler_de_texto(agent_cfg, mock_container) -> None:
    """Spec: los handlers de voz deben registrarse ANTES del handler de texto
    para que python-telegram-bot los despache correctamente."""
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(settings=agent_cfg, ports=mock_container)

    registered = [c.args[0] for c in mock_app.add_handler.call_args_list]
    # Índices donde aparece cada tipo de MessageHandler.
    text_indices = [
        i
        for i, h in enumerate(registered)
        if hasattr(h, "callback") and h.callback == bot._handle_message
    ]
    voice_indices = [
        i
        for i, h in enumerate(registered)
        if hasattr(h, "callback") and h.callback == bot._handle_voice_message
    ]
    assert text_indices, "No se registró el handler de texto"
    assert voice_indices, "No se registraron los handlers de voz"
    # TODOS los handlers de voz deben tener un índice menor al de texto.
    assert max(voice_indices) < min(text_indices), (
        f"Los handlers de voz {voice_indices} deben registrarse ANTES del de texto {text_indices}"
    )


async def test_audio_demasiado_grande_loguea_warning_con_tamano(
    agent_cfg, mock_container, caplog
) -> None:
    """SUG: cuando el audio supera el límite el handler debe loguear WARNING
    con el tamaño efectivo y el límite, para trazabilidad operativa."""
    import logging

    agent_cfg.transcription.max_audio_mb = 1
    bot = _build_bot(agent_cfg, mock_container)
    oversize = 2 * 1024 * 1024  # 2 MB
    voice = _mk_voice(bytes_result=b"x", file_size=oversize)
    update = _mk_update(voice=voice)
    context = MagicMock()

    with caplog.at_level(logging.WARNING, logger="adapters.inbound.telegram.media"):
        await bot._handle_voice_message(update, context)

    warning_texts = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    # El warning debe incluir el tamaño efectivo en bytes.
    assert any(str(oversize) in m for m in warning_texts), (
        f"Se esperaba un WARNING que incluyera {oversize} bytes; "
        f"warnings registrados: {warning_texts}"
    )
