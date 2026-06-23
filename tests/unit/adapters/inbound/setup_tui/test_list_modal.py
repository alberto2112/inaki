"""Tests de la lógica de EditListModal (parseo/validación por tipo de item).

El modal en sí es UI (Textual); acá se testea la lógica pura `_parse`/`_valido`,
que solo depende del `list_item_type` del Field y no monta la app.
"""

from __future__ import annotations

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.modals.list import EditListModal


def _modal(item_type: str) -> EditListModal:
    return EditListModal(Field(label="x", value=[], kind="list", list_item_type=item_type))


def test_parse_y_validacion_int():
    m = _modal("int")
    assert m._parse("42") == 42
    assert m._valido("42") is True
    assert m._valido("abc") is False


def test_parse_str_acepta_cualquier_texto():
    m = _modal("str")
    assert m._parse("hello") == "hello"
    assert m._valido("anything") is True


def test_parse_y_validacion_float():
    m = _modal("float")
    assert m._parse("3.14") == 3.14
    assert m._valido("3.14") is True
    assert m._valido("no-num") is False


def test_default_item_type_es_str():
    # Field sin list_item_type → el modal asume "str" (no rompe).
    m = EditListModal(Field(label="x", value=[], kind="list"))
    assert m._valido("loquesea") is True
