"""Tests para EditBoolModal y el helper coerce_bool."""

from __future__ import annotations

from adapters.inbound.setup_tui.domain.field import Field, coerce_bool
from adapters.inbound.setup_tui.modals.bool import EditBoolModal


def _make_field(*, value: object = "", default: str | None = None) -> Field:
    """Crea un Field booleano de prueba."""
    return Field(label="enabled", value=value, kind="bool", default=default)


class TestCoerceBool:
    """coerce_bool interpreta valores crudos como booleanos."""

    def test_bool_nativo_true(self):
        assert coerce_bool(True) is True

    def test_bool_nativo_false(self):
        assert coerce_bool(False) is False

    def test_string_true_variantes(self):
        for s in ("true", "True", "TRUE", "1", "yes", "on", "sí", "si"):
            assert coerce_bool(s) is True, s

    def test_string_false_variantes(self):
        for s in ("false", "False", "0", "no", "", "off", "cualquier-cosa"):
            assert coerce_bool(s) is False, s

    def test_numeros(self):
        assert coerce_bool(1) is True
        assert coerce_bool(0) is False

    def test_none_es_false(self):
        assert coerce_bool(None) is False


class TestEditBoolModalEstadoInicial:
    """El estado inicial del toggle se deriva del valor (o el default)."""

    def test_valor_bool_true(self):
        modal = EditBoolModal(_make_field(value=True))
        assert modal._estado is True

    def test_valor_bool_false(self):
        modal = EditBoolModal(_make_field(value=False))
        assert modal._estado is False

    def test_valor_string_viejo_true(self):
        """Una edición vieja pudo dejar el valor como string 'true'."""
        modal = EditBoolModal(_make_field(value="true"))
        assert modal._estado is True

    def test_vacio_cae_al_default(self):
        """Sin valor seteado, el estado inicial usa el default del schema."""
        modal = EditBoolModal(_make_field(value="", default="True"))
        assert modal._estado is True

    def test_vacio_sin_default_es_false(self):
        modal = EditBoolModal(_make_field(value=""))
        assert modal._estado is False


class TestEditBoolModalAcciones:
    """Las acciones de teclado alternan y confirman el estado."""

    def test_flip_alterna(self):
        modal = EditBoolModal(_make_field(value=False))
        assert modal._estado is False
        modal.action_flip()
        assert modal._estado is True
        modal.action_flip()
        assert modal._estado is False

    def test_render_refleja_estado(self):
        modal = EditBoolModal(_make_field(value=True))
        assert "TRUE" in modal._render_toggle()
        modal._estado = False
        assert "FALSE" in modal._render_toggle()
