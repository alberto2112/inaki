"""
Tests del dataclass FieldSpec.

Cubre: creación, valores por defecto, frozen (inmutabilidad).
"""

from __future__ import annotations

import pytest

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec


class TestFieldSpecCreacion:
    def test_campos_obligatorios(self) -> None:
        spec = FieldSpec(key="model", tipo=str)
        assert spec.key == "model"
        assert spec.tipo is str

    def test_defaults(self) -> None:
        spec = FieldSpec(key="model", tipo=str)
        assert spec.descripcion == ""
        assert spec.enum_choices is None
        assert spec.dropdown_source is None
        assert spec.placeholder == ""
        assert spec.es_tristate is False
        assert spec.es_nullable is False
        assert spec.es_lista is False

    def test_es_lista_true(self) -> None:
        spec = FieldSpec(key="targets", tipo=str, es_lista=True)
        assert spec.es_lista is True

    def test_campos_opcionales(self) -> None:
        spec = FieldSpec(
            key="provider",
            tipo=str,
            descripcion="Key del registry",
            enum_choices=("groq", "openai"),
            dropdown_source="providers",
            placeholder="groq",
        )
        assert spec.descripcion == "Key del registry"
        assert spec.enum_choices == ("groq", "openai")
        assert spec.dropdown_source == "providers"
        assert spec.placeholder == "groq"

    def test_frozen(self) -> None:
        spec = FieldSpec(key="model", tipo=str)
        with pytest.raises(Exception):
            spec.key = "otro"  # type: ignore[misc]

    def test_es_tristate_false_por_defecto(self) -> None:
        spec = FieldSpec(key="model", tipo=str)
        assert spec.es_tristate is False

    def test_es_tristate_true(self) -> None:
        spec = FieldSpec(key="model", tipo=str, es_tristate=True)
        assert spec.es_tristate is True

    def test_tipos_numericos(self) -> None:
        spec_int = FieldSpec(key="max_tokens", tipo=int)
        spec_float = FieldSpec(key="temperature", tipo=float)
        spec_bool = FieldSpec(key="enabled", tipo=bool)

        assert spec_int.tipo is int
        assert spec_float.tipo is float
        assert spec_bool.tipo is bool
