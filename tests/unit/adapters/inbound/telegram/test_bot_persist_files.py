"""Tests para la persistencia de file_id desde el TelegramBot.

Cobertura:
- Photo individual: persiste file_id con history_id.
- Album (media_group_id seteado): persiste file_id con history_id=None y NO procesa.
- Voice/audio/video_note: persisten file_id antes del size-check.
- Document/video: handlers MUDOS — solo persisten, sin reply.
- Sin repo registrado: no rompe el flujo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


from adapters.inbound.telegram.bot import TelegramBot
from core.domain.value_objects.telegram_file import TelegramFileRecord


def _make_bot(*, has_repo: bool = True, voice_enabled: bool = True, tmp_path=None):
    agent_cfg = MagicMock()
    agent_cfg.id = "test-agent"
    agent_cfg.channels.get.return_value = {
        "token": "fake-token",
        "allowed_user_ids": [],
        "voice_enabled": voice_enabled,
    }
    agent_cfg.transcription.max_audio_mb = 10
    # workspace usado por _pre_download_media (descargas en <ws>/telegram/)
    agent_cfg.workspace.path = str(tmp_path) if tmp_path else "/tmp/test-ws"

    container = MagicMock()
    container.run_agent = MagicMock()
    container.run_agent.execute = AsyncMock(return_value="ok")
    container.run_agent.set_extra_system_sections = MagicMock()
    container.run_agent.record_photo_message = AsyncMock(return_value=42)
    container.set_channel_context = MagicMock()
    container.process_photo = None  # Para que photo handler haga early return

    repo = AsyncMock() if has_repo else None
    container.telegram_file_repo = repo
    # Por defecto sin downloader → no se pre-descarga (tests legacy siguen pasando).
    container.telegram_file_downloader = None

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
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


def _document_update(chat_id: int = -100):
    update = MagicMock()
    update.effective_user.id = 42
    update.effective_chat.id = chat_id
    update.effective_chat.type = "private"

    msg = MagicMock()
    msg.media_group_id = None
    msg.photo = []
    msg.voice = None
    msg.audio = None
    msg.video = None
    msg.video_note = None
    doc = MagicMock()
    doc.file_id = "DOC-123"
    doc.file_unique_id = "DOC-uniq"
    doc.mime_type = "application/pdf"
    msg.document = doc
    msg.caption = "informe"
    msg.date = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)

    update.message = msg
    return update


# ---------------------------------------------------------------------------
# Album: persiste pero no procesa
# ---------------------------------------------------------------------------


async def test_album_persiste_file_id_y_no_procesa(monkeypatch):
    bot, container, repo = _make_bot()
    update = _photo_update(media_group_id="grupo-1")
    ctx = MagicMock()

    await bot._handle_photo_message(update, ctx)

    repo.save.assert_awaited_once()
    record: TelegramFileRecord = repo.save.call_args.args[0]
    assert record.content_type == "photo"
    assert record.file_id == "FOTO-123"
    assert record.media_group_id == "grupo-1"
    assert record.history_id is None
    # No se procesó (process_photo=None igual nos sacaba pero verificamos que no se intentó persistir history)
    container.run_agent.record_photo_message.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# Document/video: handler mudo
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
    assert user_input.startswith("__FILE__ informe.pdf")
    assert "manda este fichero por email" in user_input


async def test_handle_silent_media_video_con_caption_usa_prefijo_video():
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
    assert user_input.startswith("__VIDEO__ clip.mp4")


async def test_handle_silent_media_sin_caption_solo_persiste():
    bot, container, repo = _make_bot()
    update = _document_update()
    update.message.caption = None
    bot._run_pipeline = AsyncMock()

    await bot._handle_silent_media(update, MagicMock())

    repo.save.assert_awaited_once()
    bot._run_pipeline.assert_not_awaited()


async def test_handle_silent_media_persiste_metadata_correcta():
    bot, container, repo = _make_bot()
    update = _document_update()
    update.message.caption = None  # solo persistencia, sin pipeline

    await bot._handle_silent_media(update, MagicMock())

    record = repo.save.call_args.args[0]
    assert record.content_type == "file"
    assert record.file_id == "DOC-123"
    assert record.mime_type == "application/pdf"


async def test_album_con_caption_dispara_pipeline_en_privado(monkeypatch):
    import adapters.inbound.telegram.bot as bot_mod
    monkeypatch.setattr(bot_mod, "ALBUM_GATHER_DELAY_SEC", 0.0)

    bot, container, repo = _make_bot()
    repo.query_recent.return_value = []  # álbum vacío en repo
    update = _photo_update(media_group_id="grupo-1")
    update.message.chat = MagicMock(type="private")
    update.message.caption = "mandá esto a juan"
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_photo_message(update, MagicMock())

    repo.save.assert_awaited_once()
    bot._run_pipeline.assert_awaited_once()
    args, kwargs = bot._run_pipeline.call_args
    user_input = args[1] if len(args) > 1 else kwargs.get("user_input")
    assert user_input.startswith("__ALBUM__")
    assert "mandá esto a juan" in user_input


async def test_album_sin_caption_solo_persiste_y_no_dispara_pipeline():
    bot, container, repo = _make_bot()
    update = _photo_update(media_group_id="grupo-1")
    update.message.caption = None
    bot._run_pipeline = AsyncMock()

    await bot._handle_photo_message(update, MagicMock())

    repo.save.assert_awaited_once()
    bot._run_pipeline.assert_not_awaited()


async def test_album_con_caption_recopila_todas_las_fotos_persistidas(
    monkeypatch, tmp_path
):
    """El handler espera, lee TODAS las fotos del media_group_id y las descarga."""
    import adapters.inbound.telegram.bot as bot_mod
    from core.domain.value_objects.telegram_file import TelegramFileRecord

    monkeypatch.setattr(bot_mod, "ALBUM_GATHER_DELAY_SEC", 0.0)

    bot, container, repo = _make_bot(tmp_path=tmp_path)

    # Simulamos que en el repo ya están las 3 fotos del álbum (incluyendo la
    # que disparó este handler). El handler las junta todas.
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    records = []
    for i in range(3):
        records.append(TelegramFileRecord(
            agent_id="test-agent",
            channel="telegram",
            chat_id="-100",
            content_type="photo",
            file_id=f"ID-{i}",
            file_unique_id=f"uniq-{i}",
            media_group_id="mgrupo-X",
            mime_type="image/jpeg",
            received_at=base,
        ))
    # query_recent puede devolver también miembros de OTROS álbumes — el
    # handler debe filtrar por media_group_id.
    records.append(TelegramFileRecord(
        agent_id="test-agent", channel="telegram", chat_id="-100",
        content_type="photo", file_id="OTRA", file_unique_id="otra-uniq",
        media_group_id="otro-grupo", mime_type="image/jpeg",
        received_at=base,
    ))
    repo.query_recent.return_value = records

    async def _fake_download(*, file_id, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    fake_dl = MagicMock()
    fake_dl.download = AsyncMock(side_effect=_fake_download)
    container.telegram_file_downloader = fake_dl

    update = _photo_update(media_group_id="mgrupo-X")
    update.message.chat = MagicMock(type="private")
    update.message.caption = "mandá el álbum"
    bot._run_pipeline = AsyncMock()
    bot._set_reaction = AsyncMock()

    await bot._handle_photo_message(update, MagicMock())

    bot._run_pipeline.assert_awaited_once()
    args, kwargs = bot._run_pipeline.call_args
    user_input = args[1] if len(args) > 1 else kwargs.get("user_input")
    # 3 paths del álbum correcto (otro-grupo filtrado)
    assert "(3 photos)" in user_input
    for i in range(3):
        assert f"uniq-{i}.jpg" in user_input
    assert "otra-uniq" not in user_input
    assert "mandá el álbum" in user_input


async def test_silent_media_user_no_autorizado_no_persiste():
    bot, container, repo = _make_bot()
    bot._allowed_ids = ["999"]  # 42 no está
    update = _document_update()
    ctx = MagicMock()

    await bot._handle_silent_media(update, ctx)

    repo.save.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sin repo: no rompe
# ---------------------------------------------------------------------------


async def test_album_sin_repo_no_rompe():
    bot, container, repo = _make_bot(has_repo=False)
    update = _photo_update(media_group_id="grupo-1")
    ctx = MagicMock()
    # No debe lanzar
    await bot._handle_photo_message(update, ctx)


async def test_silent_media_sin_repo_no_rompe():
    bot, container, repo = _make_bot(has_repo=False)
    update = _document_update()
    update.message.caption = None  # solo persistencia, sin pipeline
    ctx = MagicMock()
    await bot._handle_silent_media(update, ctx)


# ---------------------------------------------------------------------------
# Persistencia falla → no rompe el handler
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
    assert str(expected_path) in user_input
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
    """Si la descarga falla, igual triggea el pipeline pero sin path."""
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
    # Sin path, pero mantiene el prefijo y el caption
    assert "__FILE__ x.pdf" in user_input
    assert "test" in user_input
    assert "/telegram/" not in user_input


async def test_voice_disabled_persiste_pero_no_transcribe():
    """voice_enabled=False NO debe bloquear la persistencia del file_id."""
    bot, container, repo = _make_bot(voice_enabled=False)
    bot._container.transcription = AsyncMock()  # no debería llamarse

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
    # NO transcribió ni respondió
    bot._container.transcription.transcribe.assert_not_awaited()
    msg.reply_text.assert_not_awaited()


async def test_persist_falla_y_no_propaga():
    bot, container, repo = _make_bot()
    repo.save.side_effect = RuntimeError("DB caída")
    update = _document_update()
    update.message.caption = None  # evitar disparar pipeline
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
