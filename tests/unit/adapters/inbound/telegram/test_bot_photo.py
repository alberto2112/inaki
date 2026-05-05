"""Tests del handler de fotos de TelegramBot (task 5.3).

Cubre:
- Usuario no autorizado → drop silencioso.
- Album guard (media_group_id seteado) → drop silencioso, use case NO llamado.
- Feature disabled (process_photo=None) → reply de aviso, no use case.
- Happy path private: reacción 👁, use case ejecutado, pipeline corrido.
- Resultado con imagen anotada → reply_photo llamado.
- should_skip_run_agent=True → reply de aviso, pipeline NO corrido.
- Error en use case → reply de error, reacción ❌.
- Handler registrado para filters.PHOTO antes del handler de texto.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain.entities.face import ProcessPhotoResult


# ---------------------------------------------------------------------------
# Helpers de construcción
# ---------------------------------------------------------------------------


def _mk_agent_cfg(*, allowed_user_ids: list[int] | None = None) -> MagicMock:
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.name = "Iñaki"
    cfg.description = "Asistente"
    tg = {
        "token": "dummy-token",
        "allowed_user_ids": allowed_user_ids or [],
        "reactions": True,
        "voice_enabled": False,  # deshabilitar voz para simplificar
    }
    cfg.channels = {"telegram": tg}
    cfg.transcription = None
    cfg.delegation = MagicMock()
    cfg.delegation.enabled = False
    return cfg


@pytest.fixture
def agent_cfg() -> MagicMock:
    return _mk_agent_cfg(allowed_user_ids=[12345])


@pytest.fixture
def mock_process_photo() -> AsyncMock:
    uc = AsyncMock()
    uc.execute.return_value = ProcessPhotoResult(
        text_context="Descripción de la escena:\nuna foto de prueba.",
        annotated_image=None,
        should_skip_run_agent=False,
    )
    return uc


@pytest.fixture
def mock_container(mock_process_photo) -> MagicMock:
    container = MagicMock()
    container.run_agent = MagicMock()
    container.run_agent.execute = AsyncMock(return_value="Respuesta del agente")
    container.run_agent.record_photo_message = AsyncMock(return_value=42)
    container.run_agent.record_assistant_message = AsyncMock()
    container.run_agent.update_message_content = AsyncMock(return_value=True)
    container.run_agent.set_extra_system_sections = MagicMock()
    container.run_agent.set_photo_debug_path = MagicMock()
    container.process_photo = mock_process_photo
    container.set_channel_context = MagicMock()
    return container


def _build_bot(agent_cfg, mock_container):
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(agent_cfg=agent_cfg, container=mock_container)


def _mk_photo_size(*, bytes_result: bytes = b"jpeg-data", file_size: int = 9999):
    ps = MagicMock()
    ps.file_size = file_size
    f = MagicMock()
    f.download_as_bytearray = AsyncMock(return_value=bytearray(bytes_result))
    ps.get_file = AsyncMock(return_value=f)
    return ps


def _mk_update(
    *,
    user_id: int = 12345,
    chat_id: int = 99,
    chat_type: str = "private",
    photos: list | None = None,
    media_group_id: str | None = None,
    caption: str | None = None,
):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type

    msg = MagicMock()
    msg.photo = photos or [_mk_photo_size()]
    msg.media_group_id = media_group_id
    msg.caption = caption
    msg.reply_text = AsyncMock()
    msg.reply_photo = AsyncMock()
    msg.set_reaction = AsyncMock()
    update.message = msg
    return update


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_user_no_autorizado_drop_silencioso(agent_cfg, mock_container) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(user_id=999)
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    mock_container.process_photo.execute.assert_not_called()
    update.message.reply_text.assert_not_called()
    update.message.reply_photo.assert_not_called()


async def test_album_guard_media_group_id_drop_silencioso(agent_cfg, mock_container) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(media_group_id="abc-123")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    mock_container.process_photo.execute.assert_not_called()
    mock_container.run_agent.record_photo_message.assert_not_called()
    update.message.reply_text.assert_not_called()
    update.message.reply_photo.assert_not_called()


async def test_feature_disabled_process_photo_none(agent_cfg) -> None:
    container = MagicMock()
    container.run_agent = MagicMock()
    container.run_agent.execute = AsyncMock(return_value="")
    container.run_agent.record_photo_message = AsyncMock(return_value=0)
    container.run_agent.set_extra_system_sections = MagicMock()
    container.process_photo = None  # feature disabled
    container.set_channel_context = MagicMock()

    bot = _build_bot(agent_cfg, container)
    update = _mk_update()
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    container.run_agent.record_photo_message.assert_not_called()
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "no está habilitad" in reply_text.lower()


async def test_feature_disabled_en_grupo_silencio_total(agent_cfg) -> None:
    """En grupos sin process_photo wired: no responder, return silencioso.

    Evita que cada bot del grupo sin la feature inunde el chat con el aviso
    cada vez que llega una foto.
    """
    container = MagicMock()
    container.run_agent = MagicMock()
    container.run_agent.record_photo_message = AsyncMock(return_value=0)
    container.process_photo = None
    container.set_channel_context = MagicMock()

    bot = _build_bot(agent_cfg, container)
    update = _mk_update(chat_type="group")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    container.run_agent.record_photo_message.assert_not_called()
    update.message.reply_text.assert_not_called()


async def test_should_skip_run_agent_en_grupo_silencio_total(agent_cfg, mock_container) -> None:
    """En grupos con photos.enabled=False en runtime: return silencioso, sin aviso al chat."""
    mock_container.process_photo.execute.return_value = ProcessPhotoResult(
        text_context="",
        annotated_image=None,
        should_skip_run_agent=True,
    )
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(chat_type="group")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    mock_container.run_agent.execute.assert_not_called()
    update.message.reply_text.assert_not_called()


async def test_happy_path_private_chat_pipeline_corrido(agent_cfg, mock_container) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(chat_type="private")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    mock_container.run_agent.record_photo_message.assert_awaited_once_with(
        "__PHOTO__", channel="telegram", chat_id=str(update.effective_chat.id)
    )
    mock_container.process_photo.execute.assert_awaited_once()
    call_kwargs = mock_container.process_photo.execute.await_args.kwargs
    assert call_kwargs["history_id"] == 42
    assert call_kwargs["chat_type"] == "private"

    # El placeholder debe haberse enriquecido vía update_message_content (Opción C):
    # el text_context reemplaza al __PHOTO__ en el row 42, y luego el pipeline corre
    # en modo history-derived (sin user_input explícito).
    mock_container.run_agent.update_message_content.assert_awaited_once()
    update_args = mock_container.run_agent.update_message_content.await_args.args
    assert update_args[0] == 42  # history_id
    mock_container.run_agent.execute.assert_awaited_once()
    assert mock_container.run_agent.execute.await_args.args[0] is None


async def test_group_chat_pipeline_corrido_con_chat_type_group(
    agent_cfg, mock_container
) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(chat_type="group")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    call_kwargs = mock_container.process_photo.execute.await_args.kwargs
    assert call_kwargs["chat_type"] == "group"
    mock_container.run_agent.execute.assert_awaited_once()


async def test_annotated_image_reply_photo_llamado(agent_cfg, mock_container) -> None:
    mock_container.process_photo.execute.return_value = ProcessPhotoResult(
        text_context="📷 Foto con caras.",
        annotated_image=b"\xff\xd8\xff",  # JPEG fake
        should_skip_run_agent=False,
    )
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update()
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    update.message.reply_photo.assert_awaited_once_with(b"\xff\xd8\xff")


async def test_sin_imagen_anotada_no_llama_reply_photo(agent_cfg, mock_container) -> None:
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update()
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    update.message.reply_photo.assert_not_called()


async def test_should_skip_run_agent_no_corre_pipeline(agent_cfg, mock_container) -> None:
    mock_container.process_photo.execute.return_value = ProcessPhotoResult(
        text_context="",
        annotated_image=None,
        should_skip_run_agent=True,
    )
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update()
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    mock_container.run_agent.execute.assert_not_called()
    update.message.reply_text.assert_awaited()


async def test_error_en_use_case_reply_error_y_reaccion_x(agent_cfg, mock_container) -> None:
    mock_container.process_photo.execute.side_effect = RuntimeError("vision crash")
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update()
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    update.message.reply_text.assert_awaited()
    reply = update.message.reply_text.await_args.args[0]
    assert "Error" in reply or "error" in reply
    # ❌ se mapea a 👎 (válido en whitelist de Telegram, ver _resolve_reaction).
    reactions_sent = [c.args[0].emoji for c in update.message.set_reaction.await_args_list]
    assert "👎" in reactions_sent


def test_bot_registra_handler_photo(agent_cfg, mock_container) -> None:
    """El __init__ del bot debe registrar un MessageHandler para filters.PHOTO."""
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(agent_cfg=agent_cfg, container=mock_container)

    registered = [c.args[0] for c in mock_app.add_handler.call_args_list]
    photo_callbacks = [
        h.callback
        for h in registered
        if hasattr(h, "callback") and h.callback == bot._handle_photo_message
    ]
    assert len(photo_callbacks) == 1, (
        f"Se esperaba 1 handler para _handle_photo_message, encontrado {len(photo_callbacks)}"
    )


async def test_caption_se_adjunta_al_contexto_del_llm(agent_cfg, mock_container) -> None:
    """Si la foto viene con caption, éste debe aparecer en el contenido enriquecido del placeholder."""
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(caption="ese es mi gato durmiendo")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    # El contenido enriquecido se persiste vía update_message_content (segundo arg posicional).
    mock_container.run_agent.update_message_content.assert_awaited_once()
    enriched_content = mock_container.run_agent.update_message_content.await_args.args[1]
    assert "ese es mi gato durmiendo" in enriched_content
    assert "Descripción del usuario:" in enriched_content


async def test_sin_caption_no_agrega_seccion_descripcion(agent_cfg, mock_container) -> None:
    """Sin caption el contenido enriquecido es solo el text_context del use case."""
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(caption=None)
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    mock_container.run_agent.update_message_content.assert_awaited_once()
    enriched_content = mock_container.run_agent.update_message_content.await_args.args[1]
    assert "Descripción del usuario:" not in enriched_content


async def test_privado_no_antepone_prefijo_sender(agent_cfg, mock_container) -> None:
    """En chat privado el contenido enriquecido NO lleva prefijo `{sender} (foto): `."""
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(chat_type="private")
    update.message.from_user = MagicMock(username="alberto", first_name="Alberto")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    enriched_content = mock_container.run_agent.update_message_content.await_args.args[1]
    assert "(foto):" not in enriched_content
    assert not enriched_content.startswith("alberto")


async def test_grupo_antepone_prefijo_sender_foto(agent_cfg, mock_container) -> None:
    """En grupo el contenido enriquecido arranca con `{sender} (foto): ` (mismo patrón que audio)."""
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(chat_type="group")
    update.message.from_user = MagicMock(username="alberto", first_name="Alberto")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    enriched_content = mock_container.run_agent.update_message_content.await_args.args[1]
    assert enriched_content.startswith("alberto (foto): ")
    # El text_context del use case sigue formando el cuerpo del mensaje.
    assert "una foto de prueba" in enriched_content


async def test_grupo_con_caption_prefijo_envuelve_caption(agent_cfg, mock_container) -> None:
    """En grupo con caption, el prefijo `{sender} (foto): ` envuelve text_context + caption."""
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(chat_type="group", caption="ese es mi gato")
    update.message.from_user = MagicMock(username="alberto", first_name="Alberto")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    enriched_content = mock_container.run_agent.update_message_content.await_args.args[1]
    assert enriched_content.startswith("alberto (foto): ")
    assert "ese es mi gato" in enriched_content
    assert "Descripción del usuario:" in enriched_content


async def test_caption_incluido_en_historial(agent_cfg, mock_container) -> None:
    """El caption debe quedar registrado en el historial junto con '[foto recibida]'."""
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(caption="paisaje montañoso")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    mock_container.run_agent.record_photo_message.assert_awaited_once()
    call_args = mock_container.run_agent.record_photo_message.await_args.args
    assert "paisaje montañoso" in call_args[0]


async def test_bang_envia_directo_al_chat_sin_pipeline(agent_cfg, mock_container) -> None:
    """Caption con '!' → resultado del descriptor directo al chat, pipeline NO invocado."""
    mock_container.process_photo.execute.return_value = ProcessPhotoResult(
        text_context="Texto extraído de la imagen.",
        annotated_image=None,
        should_skip_run_agent=False,
    )
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(caption="!transcribí este documento")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    mock_container.run_agent.execute.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with("Texto extraído de la imagen.")


async def test_bang_guarda_respuesta_en_historial(agent_cfg, mock_container) -> None:
    """Con '!', el texto se persiste con prefijo 'photo_transcription:' y el user con '__PHOTO__'."""
    mock_container.process_photo.execute.return_value = ProcessPhotoResult(
        text_context="Transcripción: hola mundo.",
        annotated_image=None,
        should_skip_run_agent=False,
    )
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(caption="!extraé el texto")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    # Mensaje de usuario contiene __PHOTO__ y el prompt
    user_content = mock_container.run_agent.record_photo_message.await_args.args[0]
    assert "__PHOTO__" in user_content
    assert "extraé el texto" in user_content

    # Mensaje de asistente tiene prefijo photo_transcription:
    mock_container.run_agent.record_assistant_message.assert_awaited_once()
    assistant_content = mock_container.run_agent.record_assistant_message.await_args.args[0]
    assert assistant_content.startswith("photo_transcription: ")
    assert "Transcripción: hola mundo." in assistant_content


async def test_bang_pasa_scene_prompt_al_use_case(agent_cfg, mock_container) -> None:
    """Con '!texto', 'texto' llega como scene_prompt al use case."""
    bot = _build_bot(agent_cfg, mock_container)
    update = _mk_update(caption="!transcribí este recibo")
    context = MagicMock()

    await bot._handle_photo_message(update, context)

    call_kwargs = mock_container.process_photo.execute.await_args.kwargs
    assert call_kwargs.get("scene_prompt") == "transcribí este recibo"
    assert call_kwargs.get("analysis_only") is False


def test_photo_handler_registrado_antes_que_texto(agent_cfg, mock_container) -> None:
    """El handler de fotos debe registrarse antes que el de texto."""
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(agent_cfg=agent_cfg, container=mock_container)

    registered = [c.args[0] for c in mock_app.add_handler.call_args_list]
    text_indices = [
        i for i, h in enumerate(registered)
        if hasattr(h, "callback") and h.callback == bot._handle_message
    ]
    photo_indices = [
        i for i, h in enumerate(registered)
        if hasattr(h, "callback") and h.callback == bot._handle_photo_message
    ]
    assert photo_indices, "No se registró el handler de foto"
    assert text_indices, "No se registró el handler de texto"
    assert max(photo_indices) < min(text_indices), (
        f"Photo handler {photo_indices} debe registrarse antes que text handler {text_indices}"
    )


# ---------------------------------------------------------------------------
# Emisión de eventos broadcast desde el handler de foto (Phase 3.3)
# ---------------------------------------------------------------------------


def _mk_agent_cfg_con_emit_photo(allowed_user_ids: list[int]) -> MagicMock:
    """AgentConfig con telegram.broadcast.emit.user_input_photo=true."""
    cfg = MagicMock()
    cfg.id = "agente_a"
    cfg.name = "Iñaki"
    cfg.description = "Asistente"
    cfg.channels = {
        "telegram": {
            "token": "dummy-token",
            "allowed_user_ids": allowed_user_ids,
            "reactions": True,
            "voice_enabled": False,
            "broadcast": {
                "behavior": "mention",
                "emit": {
                    "assistant_response": True,
                    "user_input_photo": True,
                    "user_input_voice": False,
                },
            },
        }
    }
    cfg.transcription = None
    cfg.delegation = MagicMock()
    cfg.delegation.enabled = False
    return cfg


async def test_handle_photo_grupo_dispara_emit_event_user_input_photo(mock_container):
    """En chat grupal con flag user_input_photo=true, el handler dispara _emit_event."""
    cfg = _mk_agent_cfg_con_emit_photo(allowed_user_ids=[12345])

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(agent_cfg=cfg, container=mock_container, broadcast_emitter=None)

    # Spy sobre _emit_event para verificar argumentos sin depender del emitter real
    bot._emit_event = AsyncMock()

    update = _mk_update(chat_type="group")
    update.message.from_user = MagicMock(username="alberto", first_name="Alberto")
    context = MagicMock()

    await bot._handle_photo_message(update, context)
    # Dar chance a las tareas async pendientes (asyncio.ensure_future)
    import asyncio
    await asyncio.sleep(0)

    # Buscar la llamada con event_type="user_input_photo"
    photo_calls = [
        c for c in bot._emit_event.await_args_list
        if c.kwargs.get("event_type") == "user_input_photo"
    ]
    assert len(photo_calls) == 1, (
        f"Esperaba 1 llamada user_input_photo, hubo {len(photo_calls)}: "
        f"{bot._emit_event.await_args_list}"
    )
    photo_call = photo_calls[0]
    assert photo_call.kwargs["chat_id"] == "99"
    assert photo_call.kwargs["sender"] == "alberto"
    # El content del broadcast es el text_context crudo (sin prefijo de sender):
    # los receptores aplican `_format_history_prefix` que antepone "{sender} (foto): ".
    assert "una foto de prueba" in photo_call.kwargs["content"]
    assert not photo_call.kwargs["content"].startswith("alberto (foto):")


async def test_handle_photo_modo_bang_emite_user_input_photo_sin_assistant_response():
    """Modo `!`: emite user_input_photo y NO dispara pipeline (sin assistant_response)."""
    cfg = _mk_agent_cfg_con_emit_photo(allowed_user_ids=[12345])

    container = MagicMock()
    container.process_photo = AsyncMock()
    container.process_photo.execute.return_value = ProcessPhotoResult(
        text_context="texto extraído de la foto",
        annotated_image=None,
        should_skip_run_agent=False,
    )
    container.run_agent = MagicMock()
    container.run_agent.execute = AsyncMock(return_value="no debería llamarse")
    container.run_agent.record_photo_message = AsyncMock(return_value=42)
    container.run_agent.record_assistant_message = AsyncMock()
    container.run_agent.update_message_content = AsyncMock()
    container.run_agent.set_extra_system_sections = MagicMock()
    container.run_agent.set_photo_debug_path = MagicMock()
    container.set_channel_context = MagicMock()

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(agent_cfg=cfg, container=container, broadcast_emitter=None)

    bot._emit_event = AsyncMock()

    update = _mk_update(chat_type="group", caption="!transcribí esto")
    update.message.from_user = MagicMock(username="alberto", first_name="Alberto")
    context = MagicMock()

    await bot._handle_photo_message(update, context)
    import asyncio
    await asyncio.sleep(0)

    # Pipeline NO se dispara en modo `!` — assistant_response NO se emite
    container.run_agent.execute.assert_not_called()
    # record_assistant_message SÍ se llama (escribe el text_context al historial)
    container.run_agent.record_assistant_message.assert_awaited_once()

    # Solo user_input_photo emitido — no assistant_response
    photo_calls = [
        c for c in bot._emit_event.await_args_list
        if c.kwargs.get("event_type") == "user_input_photo"
    ]
    assistant_calls = [
        c for c in bot._emit_event.await_args_list
        if c.kwargs.get("event_type") == "assistant_response"
    ]
    assert len(photo_calls) == 1
    assert len(assistant_calls) == 0
    assert photo_calls[0].kwargs["content"] == "texto extraído de la foto"
    assert photo_calls[0].kwargs["sender"] == "alberto"
