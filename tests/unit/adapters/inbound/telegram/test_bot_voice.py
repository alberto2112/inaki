"""Tests del handler de voz de TelegramBot (task 3.3).

Cubre:
- Usuario no autorizado → drop silencioso, sin invocar provider ni pipeline.
- voice_enabled=False → drop silencioso incluso con allowed.
- Audio demasiado grande → reply de error + reacción ❌, SIN llamar al provider.
- Happy path: reacción 👂, transcripción, pipeline, reply final, reacción ✅.
- TranscriptionError del provider → reply de error + reacción ❌, pipeline no corre.
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
    cfg.name = "Iñaki"
    cfg.description = "Asistente"
    tg = {
        "token": "dummy-token",
        "allowed_user_ids": allowed_user_ids or [],
        "reactions": reactions,
        "voice_enabled": voice_enabled,
    }
    cfg.channels = {"telegram": tg}
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
    container.set_channel_context = MagicMock()
    return container


def _build_bot(agent_cfg, mock_container):
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(agent_cfg=agent_cfg, container=mock_container)


def _mk_update(
    *,
    user_id: int = 12345,
    chat_id: int = 99,
    voice=None,
    audio=None,
    video_note=None,
):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    msg = MagicMock()
    msg.voice = voice
    msg.audio = audio
    msg.video_note = video_note
    msg.text = None
    msg.reply_text = AsyncMock()
    msg.set_reaction = AsyncMock()
    update.message = msg
    return update


def _mk_voice(bytes_result: bytes = b"audio-data", file_size: int = 1024):
    voice = MagicMock()
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


async def test_voice_enabled_false_drop_silencioso(agent_cfg_voice_off, mock_container) -> None:
    bot = _build_bot(agent_cfg_voice_off, mock_container)
    update = _mk_update(voice=_mk_voice())
    context = MagicMock()

    await bot._handle_voice_message(update, context)

    mock_container.transcription.transcribe.assert_not_called()
    mock_container.run_agent.execute.assert_not_called()


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
    # Pipeline invocado con el texto transcrito.
    mock_container.run_agent.execute.assert_awaited_once()
    pipe_call = mock_container.run_agent.execute.await_args
    assert pipe_call.args[0] == "hola mundo"
    # Reply final enviado.
    update.message.reply_text.assert_awaited()
    # Reacción 🔊 al inicio (transcribiendo) y ✅ al final (reactions=True).
    reactions_sent = [c.args[0] for c in update.message.set_reaction.await_args_list]
    assert "🔊" in reactions_sent
    assert "✅" in reactions_sent


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
    # Debe haber respondido al usuario con el error.
    update.message.reply_text.assert_awaited()
    reactions_sent = [c.args[0] for c in update.message.set_reaction.await_args_list]
    assert "❌" in reactions_sent


async def test_provider_raises_transcription_error(agent_cfg, mock_container) -> None:
    mock_container.transcription.transcribe.side_effect = TranscriptionError("formato no soportado")
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(voice=_mk_voice(file_size=100))
    context = MagicMock()

    await bot._handle_voice_message(update, context)

    # Pipeline NO debe correr si la transcripción falla.
    mock_container.run_agent.execute.assert_not_called()
    # Debe haber replied con el error.
    update.message.reply_text.assert_awaited()
    reactions_sent = [c.args[0] for c in update.message.set_reaction.await_args_list]
    assert "❌" in reactions_sent


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
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(agent_cfg=agent_cfg, container=mock_container)

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


def test_voice_enabled_false_no_registra_handlers_de_voz(mock_container) -> None:
    """Spec R1: si `voice_enabled=False`, los handlers de voz NO deben registrarse."""
    cfg = _mk_agent_cfg(voice_enabled=False, allowed_user_ids=[12345])
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(agent_cfg=cfg, container=mock_container)

    registered = [c.args[0] for c in mock_app.add_handler.call_args_list]
    voice_handler_callbacks = [
        h.callback
        for h in registered
        if hasattr(h, "callback") and h.callback == bot._handle_voice_message
    ]
    assert voice_handler_callbacks == [], (
        f"Con voice_enabled=False no debería registrarse ningún handler de voz; "
        f"se encontraron {len(voice_handler_callbacks)}"
    )


def test_handlers_de_voz_se_registran_antes_que_handler_de_texto(agent_cfg, mock_container) -> None:
    """Spec: los handlers de voz deben registrarse ANTES del handler de texto
    para que python-telegram-bot los despache correctamente."""
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(agent_cfg=agent_cfg, container=mock_container)

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

    with caplog.at_level(logging.WARNING, logger="adapters.inbound.telegram.bot"):
        await bot._handle_voice_message(update, context)

    warning_texts = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    # El warning debe incluir el tamaño efectivo en bytes.
    assert any(str(oversize) in m for m in warning_texts), (
        f"Se esperaba un WARNING que incluyera {oversize} bytes; "
        f"warnings registrados: {warning_texts}"
    )
