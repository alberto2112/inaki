"""Tests para TelegramFileSender."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from adapters.outbound.file_transport.telegram_file_sender import TelegramFileSender


@pytest.fixture
def fake_bot() -> AsyncMock:
    return AsyncMock()


def _sender(bot) -> TelegramFileSender:
    return TelegramFileSender(get_telegram_bot=lambda: bot)


def _file(tmp_path: Path, name: str = "x.bin") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x00\x01")
    return p


# ---------------------------------------------------------------------------
# send (individuales)
# ---------------------------------------------------------------------------


async def test_send_photo_llama_send_photo(fake_bot, tmp_path):
    sender = _sender(fake_bot)
    p = _file(tmp_path)

    await sender.send(chat_id="-100", content_type="photo", source=p, caption="ola")

    fake_bot.send_photo.assert_awaited_once()
    kwargs = fake_bot.send_photo.call_args.kwargs
    assert kwargs["chat_id"] == -100
    assert kwargs["caption"] == "ola"
    # handle abierto y cerrado
    assert kwargs["photo"].closed is True


async def test_send_audio_llama_send_audio(fake_bot, tmp_path):
    await _sender(fake_bot).send(
        chat_id="42", content_type="audio", source=_file(tmp_path), caption=None
    )
    fake_bot.send_audio.assert_awaited_once()


async def test_send_video_llama_send_video(fake_bot, tmp_path):
    await _sender(fake_bot).send(
        chat_id="42", content_type="video", source=_file(tmp_path)
    )
    fake_bot.send_video.assert_awaited_once()


async def test_send_file_llama_send_document(fake_bot, tmp_path):
    await _sender(fake_bot).send(
        chat_id="42", content_type="file", source=_file(tmp_path)
    )
    fake_bot.send_document.assert_awaited_once()


async def test_send_archivo_inexistente(fake_bot, tmp_path):
    sender = _sender(fake_bot)
    with pytest.raises(FileNotFoundError):
        await sender.send(
            chat_id="42", content_type="photo", source=tmp_path / "no.jpg"
        )
    fake_bot.send_photo.assert_not_awaited()


async def test_send_chat_id_no_entero(fake_bot, tmp_path):
    sender = _sender(fake_bot)
    with pytest.raises(ValueError, match="entero"):
        await sender.send(
            chat_id="abc", content_type="photo", source=_file(tmp_path)
        )


async def test_send_sin_bot(tmp_path):
    sender = TelegramFileSender(get_telegram_bot=lambda: None)
    with pytest.raises(RuntimeError, match="bot"):
        await sender.send(
            chat_id="42", content_type="photo", source=_file(tmp_path)
        )


async def test_send_cierra_handle_aunque_falle(fake_bot, tmp_path):
    fake_bot.send_photo.side_effect = TimeoutError("timeout")
    sender = _sender(fake_bot)
    p = _file(tmp_path)
    with pytest.raises(TimeoutError):
        await sender.send(chat_id="42", content_type="photo", source=p)
    handle = fake_bot.send_photo.call_args.kwargs["photo"]
    assert handle.closed is True


# ---------------------------------------------------------------------------
# send_album
# ---------------------------------------------------------------------------


async def test_send_album_un_solo_archivo_delega_a_send(fake_bot, tmp_path):
    sender = _sender(fake_bot)
    await sender.send_album(
        chat_id="42", sources=[_file(tmp_path, "a.jpg")], caption="ola"
    )
    fake_bot.send_photo.assert_awaited_once()
    fake_bot.send_media_group.assert_not_awaited()


async def test_send_album_multiples_llama_send_media_group(fake_bot, tmp_path):
    sender = _sender(fake_bot)
    a = _file(tmp_path, "a.jpg")
    b = _file(tmp_path, "b.jpg")
    c = _file(tmp_path, "c.jpg")

    await sender.send_album(chat_id="-100", sources=[a, b, c], caption="grupo")

    fake_bot.send_media_group.assert_awaited_once()
    kwargs = fake_bot.send_media_group.call_args.kwargs
    assert kwargs["chat_id"] == -100
    media = kwargs["media"]
    assert len(media) == 3
    # caption va en la primera
    assert media[0].caption == "grupo"
    assert getattr(media[1], "caption", None) is None


async def test_send_album_vacio_falla(fake_bot):
    sender = _sender(fake_bot)
    with pytest.raises(ValueError, match="al menos"):
        await sender.send_album(chat_id="42", sources=[])


async def test_send_album_archivo_inexistente(fake_bot, tmp_path):
    sender = _sender(fake_bot)
    a = _file(tmp_path, "a.jpg")
    with pytest.raises(FileNotFoundError):
        await sender.send_album(
            chat_id="42", sources=[a, tmp_path / "no.jpg"]
        )
    fake_bot.send_media_group.assert_not_awaited()
