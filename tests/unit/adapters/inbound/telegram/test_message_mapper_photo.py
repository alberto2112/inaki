"""Tests de extract_photo_payload (task 5.1).

Helper que detecta foto en un telegram.Message, descarga los bytes de la
mayor resolución disponible y retorna (bytes, mime, size) o None.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from adapters.inbound.telegram.message_mapper import extract_photo_payload


def _mk_message(*, photos: list | None = None):
    msg = MagicMock()
    msg.photo = photos
    return msg


def _mk_photo_size(*, bytes_result: bytes, file_size: int):
    """Mock de PhotoSize con get_file() async."""
    ps = MagicMock()
    ps.file_size = file_size
    file_mock = MagicMock()
    file_mock.download_as_bytearray = AsyncMock(return_value=bytearray(bytes_result))
    ps.get_file = AsyncMock(return_value=file_mock)
    return ps


async def test_mensaje_sin_foto_retorna_none() -> None:
    msg = _mk_message(photos=[])
    result = await extract_photo_payload(msg)
    assert result is None


async def test_mensaje_foto_none_retorna_none() -> None:
    msg = MagicMock()
    msg.photo = None
    result = await extract_photo_payload(msg)
    assert result is None


async def test_foto_unica_retorna_bytes_jpeg() -> None:
    ps = _mk_photo_size(bytes_result=b"jpeg-bytes", file_size=4321)
    msg = _mk_message(photos=[ps])

    result = await extract_photo_payload(msg)

    assert result is not None
    data, mime, size = result
    assert data == b"jpeg-bytes"
    assert mime == "image/jpeg"
    assert size == 4321


async def test_selecciona_ultima_foto_mayor_resolucion() -> None:
    """Telegram envía varias resoluciones ordenadas de menor a mayor. Debe elegir la última."""
    small = _mk_photo_size(bytes_result=b"small", file_size=100)
    large = _mk_photo_size(bytes_result=b"large-hd", file_size=90000)
    msg = _mk_message(photos=[small, large])

    data, _, _ = await extract_photo_payload(msg)
    assert data == b"large-hd"


async def test_retorna_bytes_no_bytearray() -> None:
    ps = _mk_photo_size(bytes_result=b"\xff\xd8\xff", file_size=3)
    msg = _mk_message(photos=[ps])

    data, _, _ = await extract_photo_payload(msg)
    assert isinstance(data, bytes)


async def test_file_size_none_se_normaliza_a_cero() -> None:
    ps = _mk_photo_size(bytes_result=b"x", file_size=None)
    msg = _mk_message(photos=[ps])

    _, _, size = await extract_photo_payload(msg)
    assert size == 0
