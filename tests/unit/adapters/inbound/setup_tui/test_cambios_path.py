"""Tests de los helpers de construcción de ``cambios`` por path (TUI v3).

``cambios_anidados`` y ``eliminar_en_path`` son la contraparte del árbol de
schema: un ``SchemaNode`` conoce su ``path`` real de claves YAML, así que
añadir/editar/borrar es envolver el valor (o el marcador de borrado) en ese
anidamiento.
"""

from __future__ import annotations

import pytest

from adapters.inbound.setup_tui._cambios import cambios_anidados, eliminar_en_path
from core.use_cases.config._merge import (
    CampoTriestado,
    deep_merge_con_eliminaciones,
    resolver_tristados,
)


def test_cambios_anidados_un_nivel():
    assert cambios_anidados(("token",), "T") == {"token": "T"}


def test_cambios_anidados_tres_niveles():
    assert cambios_anidados(("channels", "telegram", "groups"), {}) == {
        "channels": {"telegram": {"groups": {}}}
    }


def test_cambios_anidados_path_vacio_falla():
    with pytest.raises(ValueError, match="path no vacío"):
        cambios_anidados((), "x")


def test_eliminar_en_path_usa_marcador_de_borrado():
    cambios = eliminar_en_path(("channels", "telegram", "groups"))
    marcador = cambios["channels"]["telegram"]["groups"]
    assert isinstance(marcador, CampoTriestado)


def test_eliminar_en_path_end_to_end_poda_la_rama():
    """El cambios producido, pasado por el pipeline de merge real, elimina solo
    la clave terminal y conserva el resto."""
    base = {"channels": {"telegram": {"token": "T", "groups": {"behavior": "x"}}}}
    cambios = eliminar_en_path(("channels", "telegram", "groups"))

    resuelto = resolver_tristados(cambios)
    resultado = deep_merge_con_eliminaciones(base, resuelto)

    assert "groups" not in resultado["channels"]["telegram"]
    assert resultado["channels"]["telegram"]["token"] == "T"


def test_anadir_campo_end_to_end():
    """Añadir un campo lo inserta sin tocar los hermanos."""
    base = {"channels": {"telegram": {"token": "T"}}}
    cambios = cambios_anidados(("channels", "telegram", "reactions"), True)

    resultado = deep_merge_con_eliminaciones(base, resolver_tristados(cambios))

    assert resultado["channels"]["telegram"]["reactions"] is True
    assert resultado["channels"]["telegram"]["token"] == "T"
