"""Tests de la detección de media type por magic bytes (paquete scene)."""

from __future__ import annotations

import pytest

from inaki.perception.adapters.scene._media_type import detectar_media_type
from inaki.shared.errors import SceneDescriptionError


@pytest.mark.parametrize(
    ("cabecera", "esperado"),
    [
        (b"\xff\xd8\xff\xe0resto", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\nresto", "image/png"),
        (b"GIF87aresto", "image/gif"),
        (b"GIF89aresto", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
    ],
)
def test_detecta_formatos_soportados(cabecera: bytes, esperado: str) -> None:
    assert detectar_media_type(cabecera) == esperado


@pytest.mark.parametrize(
    "cabecera",
    [b"", b"BM\x00\x00bitmap", b"RIFF\x00\x00\x00\x00WAVEfmt ", b"%PDF-1.7"],
)
def test_formato_no_soportado_lanza_error_con_cabecera(cabecera: bytes) -> None:
    with pytest.raises(SceneDescriptionError, match="no soportado"):
        detectar_media_type(cabecera)
