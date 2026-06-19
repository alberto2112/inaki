"""Tests de los modales add/delete (TUI v3), sin montar Textual."""

from __future__ import annotations

from adapters.inbound.setup_tui.domain.schema_node import AddableOption
from adapters.inbound.setup_tui.modals.add_node import AddNodeModal, _resumen
from adapters.inbound.setup_tui.modals.confirm_delete import ConfirmDeleteModal


def _opt(key: str, is_section: bool = False, desc: str = "") -> AddableOption:
    return AddableOption(key=key, label=key, is_section=is_section, description=desc)


class TestAddNodeModal:
    def test_mapea_opciones_por_key(self):
        opts = [_opt("groups", is_section=True), _opt("token")]
        modal = AddNodeModal(opts)
        assert set(modal._by_key) == {"groups", "token"}
        assert modal._by_key["groups"].is_section is True
        assert modal._by_key["token"].is_section is False

    def test_titulo_por_defecto_y_custom(self):
        assert AddNodeModal([])._titulo == "añadir"
        assert AddNodeModal([], "añadir en telegram")._titulo == "añadir en telegram"


class TestResumen:
    def test_toma_primera_linea(self):
        assert _resumen("primera línea\nsegunda") == "primera línea"

    def test_trunca_largo(self):
        largo = "x" * 80
        out = _resumen(largo, limite=60)
        assert len(out) == 60
        assert out.endswith("…")

    def test_vacio(self):
        assert _resumen("") == ""
        assert _resumen("   ") == ""


class TestConfirmDeleteModal:
    def test_guarda_contexto_de_seccion(self):
        modal = ConfirmDeleteModal(
            "channels.telegram.groups", es_seccion=True, campos_afectados=["behavior", "rate_limiter"]
        )
        assert modal._titulo == "channels.telegram.groups"
        assert modal._es_seccion is True
        assert modal._campos == ["behavior", "rate_limiter"]

    def test_campo_simple_sin_afectados(self):
        modal = ConfirmDeleteModal("channels.telegram.token", es_seccion=False)
        assert modal._es_seccion is False
        assert modal._campos == []
