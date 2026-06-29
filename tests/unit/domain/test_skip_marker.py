"""Tests del predicado de detección del marcador ``__SKIP__``."""

from __future__ import annotations

import pytest

from core.domain.skip_marker import SKIP_MARKER, is_skip_response


def test_skip_marker_constante() -> None:
    assert SKIP_MARKER == "__SKIP__"


@pytest.mark.parametrize(
    "response",
    [
        "__SKIP__",
        "__skip__",  # case-insensitive
        "Sin novedades. __SKIP__",  # post-amble
        "__SKIP__ no hay nada que reportar",  # pre-amble
        "  __SKIP__  ",  # whitespace
    ],
)
def test_detecta_skip_tolerante(response: str) -> None:
    assert is_skip_response(response) is True


@pytest.mark.parametrize(
    "response",
    [
        "",
        "Todo en orden, te aviso si cambia algo",
        "el skip no aplica acá",  # no contiene el marcador completo
    ],
)
def test_no_detecta_skip(response: str) -> None:
    assert is_skip_response(response) is False


def test_marker_none_desactiva_deteccion() -> None:
    # Un caller con skip_marker=None nunca suprime, aunque el texto lo contenga.
    assert is_skip_response("__SKIP__", marker=None) is False


def test_marker_custom() -> None:
    assert is_skip_response("respuesta __QUIET__", marker="__QUIET__") is True
    assert is_skip_response("respuesta __SKIP__", marker="__QUIET__") is False
