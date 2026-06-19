"""Inferencia de kind='bool' en los helpers de schema de la TUI de setup.

Usa un modelo Pydantic local para que la cobertura del toggle booleano no
dependa de la estructura concreta de la config (que cambia por refactors).
"""

from __future__ import annotations

from adapters.inbound.setup_tui._schema import _infer_kind


class TestInferKindBool:
    """``_infer_kind`` clasifica los booleanos como 'bool' sin falsos positivos."""

    def test_bool_plano(self):
        assert _infer_kind("activo", bool) == "bool"

    def test_bool_opcional(self):
        """``bool | None`` (no triestado) también es 'bool'."""
        assert _infer_kind("reactions", bool | None) == "bool"

    def test_int_no_es_bool(self):
        """bool es subclase de int, pero un campo ``int`` debe quedar scalar."""
        assert _infer_kind("max_tokens", int) == "scalar"

    def test_float_no_es_bool(self):
        assert _infer_kind("temperature", float) == "scalar"

    def test_str_es_scalar(self):
        assert _infer_kind("nombre", str) == "scalar"
