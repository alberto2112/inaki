"""Inferencia de kind='bool' en el schema mapper de la TUI de setup.

Aislado a propósito de ``test_schema.py``: usa un modelo Pydantic local en vez
de los schemas reales, para que la cobertura del toggle booleano no dependa de
la estructura concreta de la config (que puede cambiar por refactors).
"""

from __future__ import annotations

from pydantic import BaseModel

from adapters.inbound.setup_tui._schema import _infer_kind, sections_for_model


class _ModeloDemo(BaseModel):
    """Modelo de prueba con un campo de cada tipo relevante."""

    activo: bool = True
    opcional: bool | None = None
    nombre: str = "x"
    umbral: float = 0.5
    cantidad: int = 3


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


class TestSectionsForModelBool:
    """A nivel modelo, los campos bool emergen con kind='bool'."""

    def test_campos_clasificados(self):
        secciones = sections_for_model(_ModeloDemo, {})
        fields = {f.label: f for _, fs in secciones for f in fs}

        assert fields["activo"].kind == "bool"
        assert fields["opcional"].kind == "bool"
        assert fields["nombre"].kind == "scalar"
        assert fields["umbral"].kind == "scalar"
        assert fields["cantidad"].kind == "scalar"
