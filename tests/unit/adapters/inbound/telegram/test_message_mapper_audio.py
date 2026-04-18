"""Tests de extract_audio_payload (task 3.4).

Helper que detecta voice/audio/video_note en un telegram.Message,
descarga los bytes y retorna (bytes, mime, size) o None.

Se mockea todo el Message para no depender del SDK.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from adapters.inbound.telegram.message_mapper import extract_audio_payload


def _mk_message(
    *,
    voice=None,
    audio=None,
    video_note=None,
):
    msg = MagicMock()
    msg.voice = voice
    msg.audio = audio
    msg.video_note = video_note
    return msg


def _mk_payload(*, bytes_result: bytes, mime_type: str | None, file_size: int):
    """Construye un mock de Voice/Audio/VideoNote con get_file() async."""
    payload = MagicMock()
    payload.mime_type = mime_type
    payload.file_size = file_size
    file_mock = MagicMock()
    file_mock.download_as_bytearray = AsyncMock(return_value=bytearray(bytes_result))
    payload.get_file = AsyncMock(return_value=file_mock)
    return payload


async def test_mensaje_sin_audio_retorna_none() -> None:
    msg = _mk_message()
    result = await extract_audio_payload(msg)
    assert result is None


async def test_voice_default_mime_es_audio_ogg() -> None:
    """Cuando voice.mime_type está ausente, el default es audio/ogg (Telegram usa OGG/Opus)."""
    voice = _mk_payload(bytes_result=b"voice-bytes", mime_type=None, file_size=1234)
    msg = _mk_message(voice=voice)

    result = await extract_audio_payload(msg)

    assert result is not None
    data, mime, size = result
    assert data == b"voice-bytes"
    assert mime == "audio/ogg"
    assert size == 1234


async def test_voice_respeta_mime_type_si_viene() -> None:
    voice = _mk_payload(
        bytes_result=b"xx", mime_type="audio/ogg; codecs=opus", file_size=10
    )
    msg = _mk_message(voice=voice)

    _, mime, _ = await extract_audio_payload(msg)
    assert mime == "audio/ogg; codecs=opus"


async def test_audio_usa_mime_type_del_payload() -> None:
    audio = _mk_payload(bytes_result=b"mp3-data", mime_type="audio/mpeg", file_size=9999)
    msg = _mk_message(audio=audio)

    data, mime, size = await extract_audio_payload(msg)
    assert data == b"mp3-data"
    assert mime == "audio/mpeg"
    assert size == 9999


async def test_audio_sin_mime_type_default_audio_mpeg() -> None:
    audio = _mk_payload(bytes_result=b"x", mime_type=None, file_size=1)
    msg = _mk_message(audio=audio)

    _, mime, _ = await extract_audio_payload(msg)
    assert mime == "audio/mpeg"


async def test_video_note_mime_es_video_mp4() -> None:
    """VideoNote siempre es MP4 (Telegram lo garantiza)."""
    vn = _mk_payload(bytes_result=b"vn-data", mime_type=None, file_size=55555)
    msg = _mk_message(video_note=vn)

    data, mime, size = await extract_audio_payload(msg)
    assert data == b"vn-data"
    assert mime == "video/mp4"
    assert size == 55555


async def test_prioridad_voice_sobre_audio_sobre_video_note() -> None:
    """Si por alguna razón vienen varios, voice > audio > video_note."""
    voice = _mk_payload(bytes_result=b"voz", mime_type=None, file_size=1)
    audio = _mk_payload(bytes_result=b"aud", mime_type="audio/mpeg", file_size=2)
    msg = _mk_message(voice=voice, audio=audio)

    data, _, _ = await extract_audio_payload(msg)
    assert data == b"voz"


async def test_retorna_bytes_no_bytearray() -> None:
    """download_as_bytearray retorna bytearray; el helper debe devolver bytes."""
    voice = _mk_payload(bytes_result=b"\x00\x01\x02", mime_type=None, file_size=3)
    msg = _mk_message(voice=voice)

    data, _, _ = await extract_audio_payload(msg)
    assert isinstance(data, bytes)


async def test_file_size_none_se_normaliza_a_cero() -> None:
    voice = _mk_payload(bytes_result=b"x", mime_type=None, file_size=None)
    msg = _mk_message(voice=voice)

    _, _, size = await extract_audio_payload(voice=voice) if False else await extract_audio_payload(msg)
    assert size == 0
