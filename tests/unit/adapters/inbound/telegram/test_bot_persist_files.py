"""Tests para la persistencia de file_id y la gramática de attachments desde el TelegramBot.

Cobertura:
- Photo individual: persiste file_id con history_id.
- Album (media_group_id): debounce por miembro, flush único con @album, miembros tardíos → rastro sin re-turno.
- Album de documentos: mismo mecanismo de coalescencia que fotos.
- Voice/audio/video_note: persisten file_id y marcador @audio en salidas tempranas.
- Document/video sin caption: depósito CON rastro (@file en history), sin turno.
- Sin repo registrado: no rompe el flujo.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


from adapters.inbound.telegram.bot import TelegramBot
from core.domain.value_objects.telegram_file import FileContentType, TelegramFileRecord


def _make_bot(*, has_repo: bool = True, voice_enabled: bool = True, tmp_path=None):
    agent_cfg = MagicMock()
    agent_cfg.id = "test-agent"
    agent_cfg.telegram = {
        "token": "fake-token",
        "allowed_user_ids": [],
        "voice_enabled": voice_enabled,
    }
    agent_cfg.transcription.max_audio_mb = 10
    # workspace usado por _pre_download_media y _save_bytes_to_workspace
    agent_cfg.workspace_path = str(tmp_path) if tmp_path else "/tmp/test-ws"

    container = MagicMock()
    container.run_agent = MagicMock()
    container.run_agent.execute = AsyncMock(return_value="ok")
    container.run_agent.set_extra_system_sections = MagicMock()
    container.run_agent.record_photo_message = AsyncMock(return_value=42)
    container.run_agent.record_user_message = AsyncMock()
    container.process_photo = None  # Para que photo handler haga early return

    repo = AsyncMock() if has_repo else None
    container.telegram_file_repo = repo
    # Por defecto sin downloader → no se pre-descarga (bloques degradan a pending).
    container.telegram_file_downloader = None
    # scope_registry: los álbumes privados toman el slot al crearse (Fix del race
    # álbum-vs-texto). Por default el slot está libre (try_mark_busy=True).
    agent_info = MagicMock()
    agent_info.id = "test-agent"
    container.run_agent.get_agent_info = MagicMock(return_value=agent_info)
    container.scope_registry = MagicMock()
    container.scope_registry.try_mark_busy = AsyncMock(return_value=True)
    container.scope_registry.mark_idle = AsyncMock()

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.build.return_value = mock_app
        bot = TelegramBot(agent_cfg, container)

    return bot, container, repo


def _photo_update(*, media_group_id: str | None = None, chat_id: int = -100):
    update = MagicMock()
    update.effective_user.id = 42
    update.effective_chat.id = chat_id
    update.effective_chat.type = "private"

    msg = MagicMock()
    msg.media_group_id = media_group_id
    photo_size = MagicMock()
    photo_size.file_id = "FOTO-123"
    photo_size.file_unique_id = "FOTO-uniq"
    photo_size.file_size = 1024
    msg.photo = [photo_size]
    msg.voice = None
    msg.audio = None
    msg.video = None
    msg.video_note = None
    msg.document = None
    msg.caption = None
    msg.date = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    msg.reply_text = AsyncMock()
    msg.reply_photo = AsyncMock()

    update.message = msg
    return update


def _document_update(chat_id: int = -100, media_group_id: str | None = None):
    update = MagicMock()
    update.effective_user.id = 42
    update.effective_chat.id = chat_id
    update.effective_chat.type = "private"

    msg = MagicMock()
    msg.media_group_id = media_group_id
    msg.photo = []
    msg.voice = None
    msg.audio = None
    msg.video = None
    msg.video_note = None
    doc = MagicMock()
    doc.file_id = "DOC-123"
    doc.file_unique_id = "DOC-uniq"
    doc.file_name = "informe.pdf"
    doc.mime_type = "application/pdf"
    msg.document = doc
    msg.caption = "informe"
    msg.date = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)

    update.message = msg
    return update


def _album_record(
    *,
    i: int = 0,
    media_group_id: str = "mgrupo-X",
    content_type: FileContentType = "photo",
    mime: str = "image/jpeg",
    caption: str | None = None,
) -> TelegramFileRecord:
    return TelegramFileRecord(
        agent_id="test-agent",
        channel="telegram",
        chat_id="-100",
        content_type=content_type,
        file_id=f"ID-{i}",
        file_unique_id=f"uniq-{i}",
        media_group_id=media_group_id,
        caption=caption,
        mime_type=mime,
        received_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


async def _await_album_flush(bot: TelegramBot, media_group_id: str) -> None:
    """Espera el flush del debounce del álbum indicado (task creado por el handler)."""
    buf = bot._album_buffers.get(media_group_id)
    if buf is not None and buf.task is not None:
        await buf.task
    # Dar chance a cualquier continuación pendiente en el loop.
    await asyncio.sleep(0)


def _flushed_album_block(container) -> str:
    """El bloque @album que el flush persistió vía record_user_message.

    Con el álbum privado sosteniendo el slot (Fix del race álbum-vs-texto), el
    flush persiste el @album como user message y corre el turno history-derived
    (``_run_pipeline(update, None)``) en vez de pasar el @album como user_input.
    """
    return container.run_agent.record_user_message.await_args.args[0]


# ---------------------------------------------------------------------------
# Album: debounce + flush único
# ---------------------------------------------------------------------------


async def test_album_persiste_file_id_sin_procesar_como_foto(monkeypatch):
    import adapters.inbound.telegram.media as media_mod

    monkeypatch.setattr(media_mod, "ALBUM_DEBOUNCE_SEC", 0.0)

    bot, container, repo = _make_bot()
    repo.query_by_media_group.return_value = []
    update = _photo_update(media_group_id="grupo-1")
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()
    ctx = MagicMock()

    await bot._handle_photo_message(update, ctx)
    await _await_album_flush(bot, "grupo-1")

    repo.save.assert_awaited_once()
    record: TelegramFileRecord = repo.save.call_args.args[0]
    assert record.content_type == "photo"
    assert record.file_id == "FOTO-123"
    assert record.media_group_id == "grupo-1"
    assert record.history_id is None
    # NO se procesa como foto individual (sin record_photo_message), pero SÍ
    # dispara el turno de álbum coalescido — history-derived con el slot tomado.
    container.run_agent.record_photo_message.assert_not_awaited()
    container.scope_registry.try_mark_busy.assert_awaited()
    container.scope_registry.mark_idle.assert_awaited()
    bot._run_pipeline.assert_awaited_once()
    assert bot._run_pipeline.call_args.args[1] is None


async def test_album_con_caption_dispara_pipeline_en_privado(monkeypatch):
    import adapters.inbound.telegram.media as media_mod

    monkeypatch.setattr(media_mod, "ALBUM_DEBOUNCE_SEC", 0.0)

    bot, container, repo = _make_bot()
    repo.query_by_media_group.return_value = []  # álbum vacío en repo → pending
    update = _photo_update(media_group_id="grupo-1")
    update.message.chat = MagicMock(type="private")
    update.message.caption = "mandá esto a juan"
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_photo_message(update, MagicMock())
    await _await_album_flush(bot, "grupo-1")

    repo.save.assert_awaited_once()
    bot._run_pipeline.assert_awaited_once()
    block = _flushed_album_block(container)
    assert block.startswith("@album")
    assert "@caption: mandá esto a juan" in block


async def test_album_sin_caption_igual_dispara_pipeline(monkeypatch):
    """Un álbum sin caption NO queda mudo — dispara el turno coalescido con
    @album para que el bot 'se entere' de las fotos."""
    import adapters.inbound.telegram.media as media_mod

    monkeypatch.setattr(media_mod, "ALBUM_DEBOUNCE_SEC", 0.0)

    bot, container, repo = _make_bot()
    repo.query_by_media_group.return_value = []
    update = _photo_update(media_group_id="grupo-1")
    update.message.caption = None
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_photo_message(update, MagicMock())
    await _await_album_flush(bot, "grupo-1")

    repo.save.assert_awaited_once()
    bot._run_pipeline.assert_awaited_once()
    assert _flushed_album_block(container).startswith("@album")


async def test_album_debounce_un_solo_flush_para_n_miembros(monkeypatch):
    """Las N fotos de un álbum comparten media_group_id; cada miembro resetea
    el timer y el flush corre UNA sola vez cuando el debounce vence."""
    import adapters.inbound.telegram.media as media_mod

    monkeypatch.setattr(media_mod, "ALBUM_DEBOUNCE_SEC", 0.05)

    bot, container, repo = _make_bot()
    repo.query_by_media_group.return_value = []
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    for _ in range(3):
        update = _photo_update(media_group_id="grupo-dedup")
        update.message.caption = None
        await bot._handle_photo_message(update, MagicMock())

    await _await_album_flush(bot, "grupo-dedup")

    # Las 3 fotos se persisten, pero solo 1 flush dispara el pipeline.
    assert repo.save.await_count == 3
    bot._run_pipeline.assert_awaited_once()
    # El slot se toma UNA sola vez (al crear el buffer, no por cada miembro).
    assert container.scope_registry.try_mark_busy.await_count == 1
    container.scope_registry.mark_idle.assert_awaited_once()


async def test_album_miembro_tardio_post_flush_persiste_rastro_sin_returno(monkeypatch):
    """Un miembro que llega DESPUÉS del cierre del álbum (bug 7-de-8) deja su
    bloque @photo en el historial sin re-disparar el turno."""
    import adapters.inbound.telegram.media as media_mod

    monkeypatch.setattr(media_mod, "ALBUM_DEBOUNCE_SEC", 0.0)

    bot, container, repo = _make_bot()
    repo.query_by_media_group.return_value = []
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    update = _photo_update(media_group_id="grupo-tardio")
    await bot._handle_photo_message(update, MagicMock())
    await _await_album_flush(bot, "grupo-tardio")
    bot._run_pipeline.assert_awaited_once()

    # El flush ya persistió el @album vía record_user_message (1ra llamada).
    assert container.run_agent.record_user_message.await_args.args[0].startswith("@album")

    # Miembro tardío del MISMO álbum, llega tras el flush.
    tardio = _photo_update(media_group_id="grupo-tardio")
    await bot._handle_photo_message(tardio, MagicMock())

    # Se persiste el file_id (2 saves) y el rastro @photo, sin segundo turno.
    assert repo.save.await_count == 2
    bot._run_pipeline.assert_awaited_once()
    # La ÚLTIMA persistencia es el straggler @photo (además del @album del flush).
    marker = container.run_agent.record_user_message.await_args.args[0]
    assert marker.startswith("@photo")


async def test_album_recopila_todos_los_miembros_del_repo(monkeypatch, tmp_path):
    """El flush lee TODOS los miembros del media_group_id (query dedicada del
    repo) y los pre-descarga: el bloque @album lista un path por miembro."""
    import adapters.inbound.telegram.media as media_mod

    monkeypatch.setattr(media_mod, "ALBUM_DEBOUNCE_SEC", 0.0)

    bot, container, repo = _make_bot(tmp_path=tmp_path)
    repo.query_by_media_group.return_value = [
        _album_record(i=0, caption="mandá el álbum"),
        _album_record(i=1),
        _album_record(i=2),
    ]

    async def _fake_download(*, file_id, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    fake_dl = MagicMock()
    fake_dl.download = AsyncMock(side_effect=_fake_download)
    container.telegram_file_downloader = fake_dl

    update = _photo_update(media_group_id="mgrupo-X")
    update.message.chat = MagicMock(type="private")
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_photo_message(update, MagicMock())
    await _await_album_flush(bot, "mgrupo-X")

    repo.query_by_media_group.assert_awaited_once_with(
        agent_id="test-agent",
        channel="telegram",
        chat_id="-100",
        media_group_id="mgrupo-X",
    )
    bot._run_pipeline.assert_awaited_once()
    block = _flushed_album_block(container)
    assert block.startswith("@album (3 items):")
    for i in range(3):
        assert f"uniq-{i}.jpg" in block
    # El caption viene del record del repo (Telegram lo pone en UNA foto).
    assert "@caption: mandá el álbum" in block


async def test_album_de_documentos_coalesce_como_fotos(monkeypatch, tmp_path):
    """Documentos enviados juntos comparten media_group_id — mismo debounce y
    bloque @album, con líneas @file por miembro."""
    import adapters.inbound.telegram.media as media_mod

    monkeypatch.setattr(media_mod, "ALBUM_DEBOUNCE_SEC", 0.0)

    bot, container, repo = _make_bot(tmp_path=tmp_path)
    repo.query_by_media_group.return_value = [
        _album_record(i=0, media_group_id="docs-1", content_type="file", mime="application/pdf"),
        _album_record(i=1, media_group_id="docs-1", content_type="file", mime="application/pdf"),
    ]

    async def _fake_download(*, file_id, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"pdf")

    fake_dl = MagicMock()
    fake_dl.download = AsyncMock(side_effect=_fake_download)
    container.telegram_file_downloader = fake_dl

    update = _document_update(media_group_id="docs-1")
    update.message.chat = MagicMock(type="private")
    update.message.caption = None
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_silent_media(update, MagicMock())
    await _await_album_flush(bot, "docs-1")

    bot._run_pipeline.assert_awaited_once()
    block = _flushed_album_block(container)
    assert block.startswith("@album (2 items):")
    assert "@file (application/pdf) at" in block


async def test_texto_durante_album_no_arranca_turno_ciego(monkeypatch):
    """Bug reportado: el usuario manda un álbum y enseguida 'ahí están'. Con el
    álbum sosteniendo el slot del scope, ese texto cae al camino in-flight
    (record_user_message + ACK) en vez de arrancar un turno CIEGO que responde
    'no me llegó nada' porque corre antes de que exista el bloque @album.
    """
    import adapters.inbound.telegram.media as media_mod
    from adapters.outbound.scope_registry_adapter import InMemoryScopeRegistryAdapter

    # Debounce largo: el álbum NO flushea durante el test — queremos observar el
    # estado con el slot AÚN tomado cuando llega el texto.
    monkeypatch.setattr(media_mod, "ALBUM_DEBOUNCE_SEC", 30.0)

    bot, container, repo = _make_bot()
    repo.query_by_media_group.return_value = []
    # Registry REAL (no el mock que siempre devuelve True): modela busy/idle.
    container.scope_registry = InMemoryScopeRegistryAdapter()
    bot._set_reaction = AsyncMock()

    # 1) Llega el álbum → toma el slot (privado), agenda el flush (no dispara).
    album_update = _photo_update(media_group_id="grupo-race", chat_id=-100)
    await bot._handle_photo_message(album_update, MagicMock())
    assert bot._album_buffers["grupo-race"].slot_held is True

    # 2) Llega el texto "ahí están" sobre el MISMO chat.
    text_update = MagicMock()
    text_update.effective_user.id = 42
    text_update.effective_chat.id = -100
    text_update.effective_chat.type = "private"
    tmsg = text_update.message
    tmsg.text = "ahí están"
    tmsg.chat.type = "private"
    tmsg.chat.id = -100
    tmsg.location = None
    tmsg.reply_text = AsyncMock()
    tmsg.set_reaction = AsyncMock()

    await bot._handle_message(text_update, MagicMock())

    # El texto NO arrancó un turno (execute no se llamó) — cayó a in-flight.
    container.run_agent.execute.assert_not_awaited()
    recorded = [c.args[0] for c in container.run_agent.record_user_message.await_args_list]
    assert "ahí están" in recorded
    # Y el usuario recibió el ACK, no una respuesta ciega.
    tmsg.reply_text.assert_awaited_once()

    # Cleanup: cancelar el flush pendiente (debounce de 30s).
    buf = bot._album_buffers.get("grupo-race")
    if buf is not None and buf.task is not None:
        buf.task.cancel()


# ---------------------------------------------------------------------------
# Document/video: depósito con rastro
# ---------------------------------------------------------------------------


async def test_handle_silent_media_con_caption_dispara_pipeline_en_privado():
    bot, container, repo = _make_bot()
    update = _document_update()
    update.message.chat = MagicMock(type="private")
    update.message.document.file_name = "informe.pdf"
    update.message.caption = "manda este fichero por email"

    ctx = MagicMock()
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_silent_media(update, ctx)

    repo.save.assert_awaited_once()
    bot._run_pipeline.assert_awaited_once()
    args, kwargs = bot._run_pipeline.call_args
    user_input = args[1] if len(args) > 1 else kwargs.get("user_input")
    assert user_input.startswith("@file informe.pdf")
    assert "@caption: manda este fichero por email" in user_input


async def test_handle_silent_media_video_con_caption_usa_bloque_video():
    bot, container, repo = _make_bot()
    update = _document_update()
    update.message.document = None
    video = MagicMock()
    video.file_id = "VID"
    video.file_unique_id = "VIDu"
    video.mime_type = "video/mp4"
    video.file_name = "clip.mp4"
    update.message.video = video
    update.message.chat = MagicMock(type="private")
    update.message.caption = "qué dice acá?"

    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_silent_media(update, MagicMock())

    args, kwargs = bot._run_pipeline.call_args
    user_input = args[1] if len(args) > 1 else kwargs.get("user_input")
    assert user_input.startswith("@video clip.mp4")


async def test_handle_silent_media_sin_caption_persiste_rastro_sin_turno():
    """El depósito sin caption ya NO es invisible: deja el bloque @file en el
    historial (role=user) sin disparar turno — fix del bug del 'audio viejo'."""
    bot, container, repo = _make_bot()
    update = _document_update()
    update.message.chat = MagicMock(type="private")
    update.message.document.file_name = "datos.pdf"
    update.message.caption = None
    bot._run_pipeline = AsyncMock()

    await bot._handle_silent_media(update, MagicMock())

    repo.save.assert_awaited_once()
    bot._run_pipeline.assert_not_awaited()
    container.run_agent.record_user_message.assert_awaited_once()
    marker = container.run_agent.record_user_message.await_args.args[0]
    assert marker.startswith("@file datos.pdf")
    # Sin downloader el bloque degrada a pending con el id estable.
    assert "pending (id: DOC-uniq)" in marker


async def test_handle_silent_media_persiste_metadata_correcta():
    bot, container, repo = _make_bot()
    update = _document_update()
    update.message.chat = MagicMock(type="private")
    update.message.caption = None  # depósito: persiste rastro, sin pipeline

    await bot._handle_silent_media(update, MagicMock())

    record = repo.save.call_args.args[0]
    assert record.content_type == "file"
    assert record.file_id == "DOC-123"
    assert record.mime_type == "application/pdf"


async def test_silent_media_user_no_autorizado_no_persiste():
    bot, container, repo = _make_bot()
    bot._allowed_ids = ["999"]  # 42 no está
    update = _document_update()
    ctx = MagicMock()

    await bot._handle_silent_media(update, ctx)

    repo.save.assert_not_awaited()
    container.run_agent.record_user_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sin repo: no rompe
# ---------------------------------------------------------------------------


async def test_album_sin_repo_no_rompe(monkeypatch):
    import adapters.inbound.telegram.media as media_mod

    monkeypatch.setattr(media_mod, "ALBUM_DEBOUNCE_SEC", 0.0)

    bot, container, repo = _make_bot(has_repo=False)
    update = _photo_update(media_group_id="grupo-1")
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()
    ctx = MagicMock()
    # No debe lanzar; con repo None el álbum igual dispara (bloque pending).
    await bot._handle_photo_message(update, ctx)
    await _await_album_flush(bot, "grupo-1")
    bot._run_pipeline.assert_awaited_once()


async def test_silent_media_sin_repo_no_rompe():
    bot, container, repo = _make_bot(has_repo=False)
    update = _document_update()
    update.message.chat = MagicMock(type="private")
    update.message.caption = None  # depósito: rastro sin pipeline
    ctx = MagicMock()
    await bot._handle_silent_media(update, ctx)
    # El rastro @file se persiste igual aunque no haya repo de transporte.
    container.run_agent.record_user_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# Pre-descarga
# ---------------------------------------------------------------------------


async def test_pre_descarga_inyecta_path_real_en_user_input(tmp_path):
    """Cuando hay downloader y caption, el user_input lleva el path absoluto de la descarga."""
    bot, container, repo = _make_bot(tmp_path=tmp_path)
    update = _document_update()
    update.message.chat = MagicMock(type="private")
    update.message.document.file_name = "informe.pdf"
    update.message.caption = "manda esto por email"

    # Stub del downloader: crea el archivo en el dest pedido
    async def _fake_download(*, file_id, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"PDF")

    fake_dl = MagicMock()
    fake_dl.download = AsyncMock(side_effect=_fake_download)
    container.telegram_file_downloader = fake_dl

    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_silent_media(update, MagicMock())

    fake_dl.download.assert_awaited_once()
    args, kwargs = bot._run_pipeline.call_args
    user_input = args[1] if len(args) > 1 else kwargs.get("user_input")
    # El path debe estar en el user_input
    expected_path = tmp_path / "telegram" / "DOC-uniq.pdf"
    assert f"at {expected_path}" in user_input
    assert expected_path.exists()


async def test_pre_descarga_cache_hit_no_re_descarga(tmp_path):
    """Si el archivo ya existe (file_unique_id estable), no re-descarga."""
    bot, container, repo = _make_bot(tmp_path=tmp_path)
    update = _document_update()
    update.message.chat = MagicMock(type="private")
    update.message.caption = "test"

    fake_dl = MagicMock()
    fake_dl.download = AsyncMock()
    container.telegram_file_downloader = fake_dl

    # Pre-creamos el archivo destino
    (tmp_path / "telegram").mkdir()
    (tmp_path / "telegram" / "DOC-uniq.pdf").write_bytes(b"existente")

    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_silent_media(update, MagicMock())

    fake_dl.download.assert_not_awaited()


async def test_pre_descarga_falla_no_rompe_pipeline(tmp_path):
    """Si la descarga falla, igual triggea el pipeline con bloque pending."""
    bot, container, repo = _make_bot(tmp_path=tmp_path)
    update = _document_update()
    update.message.chat = MagicMock(type="private")
    update.message.document.file_name = "x.pdf"
    update.message.caption = "test"

    fake_dl = MagicMock()
    fake_dl.download = AsyncMock(side_effect=TimeoutError("net down"))
    container.telegram_file_downloader = fake_dl

    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_silent_media(update, MagicMock())

    bot._run_pipeline.assert_awaited_once()
    args, kwargs = bot._run_pipeline.call_args
    user_input = args[1] if len(args) > 1 else kwargs.get("user_input")
    # Sin path, pero mantiene el bloque degradado y el caption
    assert "@file x.pdf" in user_input
    assert "pending (id: DOC-uniq)" in user_input
    assert "@caption: test" in user_input
    assert "/telegram/" not in user_input


async def test_voice_disabled_persiste_pero_no_transcribe():
    """voice_enabled=False NO debe bloquear la persistencia del file_id ni el
    rastro @audio en el historial."""
    bot, container, repo = _make_bot(voice_enabled=False)
    bot._ports.transcription = AsyncMock()  # no debería llamarse

    update = MagicMock()
    update.effective_user.id = 42
    update.effective_chat.id = -100
    update.effective_chat.type = "private"
    msg = MagicMock()
    msg.media_group_id = None
    msg.photo = []
    voice = MagicMock()
    voice.file_id = "VOZ"
    voice.file_unique_id = "VOZu"
    voice.file_name = None
    voice.mime_type = "audio/ogg"
    voice.file_size = 1024
    msg.voice = voice
    msg.audio = None
    msg.video = None
    msg.video_note = None
    msg.document = None
    msg.caption = None
    msg.date = datetime(2026, 5, 1, tzinfo=timezone.utc)
    msg.reply_text = AsyncMock()
    update.message = msg
    ctx = MagicMock()

    await bot._handle_voice_message(update, ctx)

    repo.save.assert_awaited_once()
    record = repo.save.call_args.args[0]
    assert record.content_type == "audio"
    assert record.file_id == "VOZ"
    # NO transcribió ni respondió, pero dejó el rastro @audio.
    bot._ports.transcription.transcribe.assert_not_awaited()
    msg.reply_text.assert_not_awaited()
    container.run_agent.record_user_message.assert_awaited_once()
    marker = container.run_agent.record_user_message.await_args.args[0]
    assert marker.startswith("@audio")


async def test_persist_falla_y_no_propaga():
    bot, container, repo = _make_bot()
    repo.save.side_effect = RuntimeError("DB caída")
    update = _document_update()
    update.message.chat = MagicMock(type="private")
    update.message.caption = None  # depósito: rastro sin pipeline
    ctx = MagicMock()
    # No debe lanzar
    await bot._handle_silent_media(update, ctx)


# ---------------------------------------------------------------------------
# extract_file_metadata: tipos correctos
# ---------------------------------------------------------------------------


def test_extract_metadata_photo():
    bot, _, _ = _make_bot()
    msg = _photo_update().message
    out = bot._extract_file_metadata(msg)
    assert out is not None
    content_type, payload, mime = out
    assert content_type == "photo"
    assert payload.file_id == "FOTO-123"
    assert mime == "image/jpeg"


def test_extract_metadata_document():
    bot, _, _ = _make_bot()
    msg = _document_update().message
    out = bot._extract_file_metadata(msg)
    assert out is not None
    assert out[0] == "file"
    assert out[2] == "application/pdf"


def test_extract_metadata_document_con_mime_audio_es_audio():
    """Un mp3 adjuntado 'como archivo' se clasifica como audio, no como file —
    Telegram clasifica según cómo lo mandó el cliente, no por el contenido."""
    bot, _, _ = _make_bot()
    update = _document_update()
    update.message.document.mime_type = "audio/mpeg"
    out = bot._extract_file_metadata(update.message)
    assert out is not None
    assert out[0] == "audio"
    assert out[2] == "audio/mpeg"


def test_extract_metadata_video():
    bot, _, _ = _make_bot()
    update = _document_update()
    update.message.document = None
    video = MagicMock()
    video.file_id = "VID"
    video.file_unique_id = "VIDu"
    video.mime_type = "video/mp4"
    update.message.video = video
    out = bot._extract_file_metadata(update.message)
    assert out is not None
    assert out[0] == "video"


def test_extract_metadata_voice():
    bot, _, _ = _make_bot()
    update = _document_update()
    update.message.document = None
    voice = MagicMock()
    voice.file_id = "VOICE"
    voice.file_unique_id = "Vu"
    voice.mime_type = "audio/ogg"
    update.message.voice = voice
    out = bot._extract_file_metadata(update.message)
    assert out is not None
    assert out[0] == "audio"
    assert out[2] == "audio/ogg"


def test_extract_metadata_devuelve_none_sin_media():
    bot, _, _ = _make_bot()
    msg = MagicMock()
    msg.photo = []
    msg.voice = None
    msg.audio = None
    msg.video = None
    msg.video_note = None
    msg.document = None
    out = bot._extract_file_metadata(msg)
    assert out is None
