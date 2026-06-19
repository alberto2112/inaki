"""Tests para los helpers de AgentDetailPage.

Foco: _coerce — coerción int → float → str (usada al persistir un override
tri-estado). No requiere montar la pantalla en Textual.
"""

from __future__ import annotations

import pytest

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.screens.agent_detail_page import _coerce


def _make_scalar_field(label: str = "temperature") -> Field:
    return Field(label=label, value="", kind="scalar")


def _make_secret_field(label: str = "api_key") -> Field:
    return Field(label=label, value="", kind="secret")


class TestCoerceValue:
    """_coerce: orden de coerción int → float → str."""

    def test_entero_como_string_retorna_int(self):
        field = _make_scalar_field("max_tokens")
        result = _coerce(field, "100")
        assert result == 100
        assert isinstance(result, int)

    def test_float_como_string_retorna_float(self):
        field = _make_scalar_field("temperature")
        result = _coerce(field, "0.7")
        assert result == pytest.approx(0.7)
        assert isinstance(result, float)

    def test_string_puro_retorna_str(self):
        field = _make_scalar_field("provider")
        result = _coerce(field, "openai")
        assert result == "openai"
        assert isinstance(result, str)

    def test_vacio_retorna_str_vacio(self):
        field = _make_scalar_field()
        result = _coerce(field, "")
        assert result == ""
        assert isinstance(result, str)

    def test_campo_secret_no_coerce_a_numero(self):
        """Campos de kind != 'scalar' retornan el string tal cual."""
        field = _make_secret_field()
        result = _coerce(field, "123")
        # Campos no-scalar no intentan coerción numérica
        assert result == "123"
        assert isinstance(result, str)

    def test_cero_retorna_int(self):
        field = _make_scalar_field()
        result = _coerce(field, "0")
        assert result == 0
        assert isinstance(result, int)

    def test_negativo_retorna_int(self):
        field = _make_scalar_field()
        result = _coerce(field, "-5")
        assert result == -5
        assert isinstance(result, int)

    def test_float_negativo_retorna_float(self):
        field = _make_scalar_field()
        result = _coerce(field, "-0.5")
        assert result == pytest.approx(-0.5)
        assert isinstance(result, float)

    def test_booleano_como_string_retorna_str(self):
        """'True'/'False' no son números — retornan str."""
        field = _make_scalar_field("enabled")
        result = _coerce(field, "True")
        assert result == "True"
        assert isinstance(result, str)
