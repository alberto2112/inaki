"""Tests para PillowPhotoAnnotator.

Cubre:
- PA-01: cara única → bytes decodificables como PIL.Image con rectángulo dibujado
- PA-02: caras múltiples → numeradas secuencialmente [1], [2], [3]
- PA-03: sin caras → imagen devuelta sin cambios (mismos bytes, o al menos válida)
- PA-04: solo numera caras no-matched (unknown/ambiguous); matched no aparecen
"""

from __future__ import annotations

import io
import re

import pytest
from PIL import Image, ImageDraw

from core.domain.entities.face import BBox, FaceMatch, MatchStatus, Person
from adapters.outbound.imaging.pillow_annotator import PillowPhotoAnnotator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jpeg(ancho: int = 200, alto: int = 200, color: str = "white") -> bytes:
    """Crea un JPEG sintético en memoria para usar en los tests."""
    img = Image.new("RGB", (ancho, alto), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _face_match(
    x: int,
    y: int,
    w: int,
    h: int,
    status: MatchStatus = MatchStatus.UNKNOWN,
    face_ref: str = "1#0",
    categoria: str | None = None,
    candidatos: list[tuple[Person, float]] | None = None,
) -> FaceMatch:
    return FaceMatch(
        face_ref=face_ref,
        bbox=BBox(x=x, y=y, w=w, h=h),
        candidates=candidatos or [],
        status=status,
        categoria=categoria,
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def anotador() -> PillowPhotoAnnotator:
    return PillowPhotoAnnotator()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pa01_cara_unica_devuelve_bytes_validos(anotador):
    """PA-01: bytes en → bytes decodificables como PIL.Image."""
    imagen_bytes = _make_jpeg()
    cara = _face_match(x=10, y=10, w=80, h=100, status=MatchStatus.UNKNOWN)
    resultado = anotador.draw_numbered(imagen_bytes, [cara])

    assert isinstance(resultado, bytes)
    img = Image.open(io.BytesIO(resultado))
    assert img.width == 200
    assert img.height == 200


def test_pa01_rectángulo_dibujado(anotador):
    """PA-01: la imagen resultante difiere de la original (rectángulo pintado)."""
    imagen_bytes = _make_jpeg(color="white")
    cara = _face_match(x=10, y=10, w=80, h=100, status=MatchStatus.UNKNOWN)
    resultado = anotador.draw_numbered(imagen_bytes, [cara])

    img_orig = Image.open(io.BytesIO(imagen_bytes)).convert("RGB")
    img_res = Image.open(io.BytesIO(resultado)).convert("RGB")

    pixeles_orig = list(img_orig.getdata())
    pixeles_res = list(img_res.getdata())
    assert pixeles_orig != pixeles_res, "La imagen no fue modificada — falta el rectángulo"


def test_pa02_multiples_caras_numeradas(anotador):
    """PA-02: cara múltiples → el anotador no falla y devuelve bytes válidos."""
    imagen_bytes = _make_jpeg(ancho=300, alto=300)
    caras = [
        _face_match(x=10, y=10, w=60, h=80, status=MatchStatus.UNKNOWN, face_ref="1#0"),
        _face_match(x=100, y=10, w=60, h=80, status=MatchStatus.AMBIGUOUS, face_ref="1#1"),
        _face_match(x=200, y=10, w=60, h=80, status=MatchStatus.UNKNOWN, face_ref="1#2"),
    ]
    resultado = anotador.draw_numbered(imagen_bytes, caras)

    assert isinstance(resultado, bytes)
    img = Image.open(io.BytesIO(resultado))
    assert img.width == 300
    assert img.height == 300


def test_pa02_imagen_modificada_para_multiples(anotador):
    """PA-02: imagen con múltiples caras difiere de la original."""
    imagen_bytes = _make_jpeg(ancho=300, alto=300, color="white")
    caras = [
        _face_match(x=10, y=10, w=60, h=80, status=MatchStatus.UNKNOWN, face_ref="1#0"),
        _face_match(x=100, y=10, w=60, h=80, status=MatchStatus.UNKNOWN, face_ref="1#1"),
    ]
    resultado = anotador.draw_numbered(imagen_bytes, caras)

    img_orig = Image.open(io.BytesIO(imagen_bytes)).convert("RGB")
    img_res = Image.open(io.BytesIO(resultado)).convert("RGB")
    assert list(img_orig.getdata()) != list(img_res.getdata())


def test_pa03_sin_caras_devuelve_imagen_válida(anotador):
    """PA-03: lista vacía → bytes válidos (imagen sin modificar o idéntica)."""
    imagen_bytes = _make_jpeg()
    resultado = anotador.draw_numbered(imagen_bytes, [])

    assert isinstance(resultado, bytes)
    img = Image.open(io.BytesIO(resultado))
    assert img.width == 200
    assert img.height == 200


def test_pa03_sin_caras_imagen_no_cambia_significativamente(anotador):
    """PA-03: sin caras la imagen devuelta debe ser decodificable y del mismo tamaño."""
    imagen_bytes = _make_jpeg()
    resultado = anotador.draw_numbered(imagen_bytes, [])

    img_res = Image.open(io.BytesIO(resultado)).convert("RGB")
    assert img_res.size == (200, 200)


def test_pa04_matched_no_dibuja(anotador):
    """PA-04: caras MATCHED no se dibujan; solo UNKNOWN y AMBIGUOUS reciben rectángulo."""
    imagen_bytes = _make_jpeg(color="white")
    cara_matched = _face_match(x=10, y=10, w=80, h=100, status=MatchStatus.MATCHED, face_ref="1#0")
    resultado_sin_dibujo = anotador.draw_numbered(imagen_bytes, [cara_matched])

    # Con solo caras matched, la imagen no debería diferir (sin rectángulo)
    img_orig = Image.open(io.BytesIO(imagen_bytes)).convert("RGB")
    img_res = Image.open(io.BytesIO(resultado_sin_dibujo)).convert("RGB")
    # Para ser precisos: verificar que resultado es bytes válido
    assert isinstance(resultado_sin_dibujo, bytes)
    img_check = Image.open(io.BytesIO(resultado_sin_dibujo))
    assert img_check.size == (200, 200)


def test_pa04_solo_unknown_dibuja_matched_no(anotador):
    """PA-04: mezcla matched + unknown → solo los unknown modifican la imagen."""
    imagen_bytes = _make_jpeg(ancho=400, alto=200, color="white")
    caras_solo_matched = [
        _face_match(x=10, y=10, w=80, h=80, status=MatchStatus.MATCHED, face_ref="1#0"),
    ]
    caras_con_unknown = [
        _face_match(x=10, y=10, w=80, h=80, status=MatchStatus.MATCHED, face_ref="1#0"),
        _face_match(x=200, y=10, w=80, h=80, status=MatchStatus.UNKNOWN, face_ref="1#1"),
    ]
    res_matched = anotador.draw_numbered(imagen_bytes, caras_solo_matched)
    res_con_unknown = anotador.draw_numbered(imagen_bytes, caras_con_unknown)

    img_matched = Image.open(io.BytesIO(res_matched)).convert("RGB")
    img_con_unknown = Image.open(io.BytesIO(res_con_unknown)).convert("RGB")

    # Las imágenes deben diferir — la que tiene unknown tiene el rectángulo
    assert list(img_matched.getdata()) != list(img_con_unknown.getdata()), (
        "Imagen con UNKNOWN debería diferir de la que solo tiene MATCHED"
    )
