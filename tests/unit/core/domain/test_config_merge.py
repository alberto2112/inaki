"""Guard del motor único de merge de capas (``core/domain/config_merge.py``).

Cada caso de la tabla de semántica del módulo tiene su test acá: esta es la
definición ejecutable de qué significa "mergear capas" en todo el sistema —
carril de carga, carril de edición del setup TUI y sub-agentes efímeros.
"""

from __future__ import annotations

import pytest

from core.domain.config_merge import (
    SENTINEL_ELIMINAR,
    Capa,
    deep_merge,
    merge_capas,
    resolver_inherit,
)
from core.domain.errors import ConfigError


# ---------------------------------------------------------------------------
# Tabla de semántica
# ---------------------------------------------------------------------------


def test_dict_con_dict_se_funden_recursivamente() -> None:
    base = {"llm": {"provider": "groq", "model": "viejo", "temperature": 0.7}}
    override = {"llm": {"model": "nuevo"}}

    assert deep_merge(base, override) == {
        "llm": {"provider": "groq", "model": "nuevo", "temperature": 0.7}
    }


def test_clave_ausente_en_override_se_hereda() -> None:
    assert deep_merge({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


def test_listas_se_reemplazan_enteras_nunca_se_concatenan() -> None:
    """Footgun documentado: una capa que redefine la lista pierde la anterior."""
    base = {"knowledge": {"sources": [{"id": "a"}, {"id": "b"}]}}
    override = {"knowledge": {"sources": [{"id": "c"}]}}

    assert deep_merge(base, override)["knowledge"]["sources"] == [{"id": "c"}]


def test_none_explicito_pisa_porque_es_desactivar() -> None:
    assert deep_merge({"transcription": {"provider": "groq"}}, {"transcription": None}) == {
        "transcription": None
    }


def test_none_en_base_no_impide_encender_el_bloque() -> None:
    assert deep_merge({"transcription": None}, {"transcription": {"provider": "groq"}}) == {
        "transcription": {"provider": "groq"}
    }


def test_el_sentinel_borra_la_clave() -> None:
    assert deep_merge({"a": 1, "b": 2}, {"b": SENTINEL_ELIMINAR}) == {"a": 1}


def test_el_sentinel_sobre_una_clave_inexistente_es_no_op() -> None:
    assert deep_merge({"a": 1}, {"z": SENTINEL_ELIMINAR}) == {"a": 1}


def test_no_muta_los_argumentos() -> None:
    base = {"llm": {"model": "viejo"}}
    override = {"llm": {"model": "nuevo"}}

    deep_merge(base, override)

    assert base == {"llm": {"model": "viejo"}}
    assert override == {"llm": {"model": "nuevo"}}


# ---------------------------------------------------------------------------
# Conflicto de forma — lo que antes pasaba en silencio
# ---------------------------------------------------------------------------


def test_escalar_pisando_un_bloque_es_error_con_su_path() -> None:
    with pytest.raises(ConfigError, match="llm.model"):
        deep_merge({"llm": {"model": {"nombre": "x"}}}, {"llm": {"model": "gpt"}})


def test_bloque_pisando_un_escalar_es_error_con_su_path() -> None:
    with pytest.raises(ConfigError, match="llm.model"):
        deep_merge({"llm": {"model": "gpt"}}, {"llm": {"model": {"nombre": "x"}}})


def test_una_clave_nueva_nunca_es_conflicto() -> None:
    """Solo hay conflicto si la clave YA existía con otra forma."""
    assert deep_merge({"a": 1}, {"b": {"c": 2}}) == {"a": 1, "b": {"c": 2}}


def test_lista_pisando_lista_no_es_conflicto() -> None:
    assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}


# ---------------------------------------------------------------------------
# merge_capas — orden y procedencia
# ---------------------------------------------------------------------------


def test_global_es_la_base_y_el_resto_completa_o_pisa() -> None:
    """La invariante del sistema: global.yaml arranca, las siguientes completan."""
    resultado = merge_capas(
        [
            Capa("global", {"llm": {"provider": "groq", "model": "base"}, "app": {"name": "I"}}),
            Capa("agent", {"llm": {"model": "override"}}),
        ]
    )

    assert resultado.datos == {
        "llm": {"provider": "groq", "model": "override"},
        "app": {"name": "I"},
    }


def test_la_procedencia_dice_de_que_capa_salio_cada_hoja() -> None:
    resultado = merge_capas(
        [
            Capa("global", {"llm": {"provider": "groq", "model": "base"}}),
            Capa("agent", {"llm": {"model": "override"}}),
        ]
    )

    assert resultado.procedencia["llm.provider"] == "global"
    assert resultado.procedencia["llm.model"] == "agent"


def test_la_procedencia_registra_la_ultima_capa_que_escribio() -> None:
    resultado = merge_capas(
        [
            Capa("a", {"x": 1}),
            Capa("b", {"x": 2}),
            Capa("c", {"y": 3}),
        ]
    )

    assert resultado.procedencia == {"x": "b", "y": "c"}


def test_una_capa_vacia_no_aporta_procedencia() -> None:
    resultado = merge_capas([Capa("global", {"a": 1}), Capa("agent", {})])

    assert resultado.datos == {"a": 1}
    assert resultado.procedencia == {"a": "global"}


def test_un_bloque_declarado_vacio_tiene_procedencia() -> None:
    """Declarar `groups: {}` es una decisión del operador, no una ausencia."""
    resultado = merge_capas([Capa("agent", {"groups": {}})])

    assert resultado.procedencia["groups"] == "agent"


def test_la_procedencia_se_poda_cuando_el_sentinel_borra() -> None:
    resultado = merge_capas(
        [
            Capa("global", {"llm": {"model": "m", "temperature": 0.7}}),
            Capa("agent", {"llm": {"model": SENTINEL_ELIMINAR}}),
        ]
    )

    assert resultado.datos == {"llm": {"temperature": 0.7}}
    assert "llm.model" not in resultado.procedencia
    assert resultado.procedencia["llm.temperature"] == "global"


def test_el_conflicto_nombra_la_capa_culpable() -> None:
    with pytest.raises(ConfigError, match="agent"):
        merge_capas([Capa("global", {"llm": {"model": "m"}}), Capa("agent", {"llm": "gpt"})])


# ---------------------------------------------------------------------------
# resolver_inherit — herencia opt-in por bloque
# ---------------------------------------------------------------------------


def test_inherit_true_mergea_el_bloque_del_padre_bajo_el_del_hijo() -> None:
    hijo = resolver_inherit(
        {"llm": {"inherit": True, "model": "propio"}},
        {"llm": {"provider": "groq", "model": "del-padre", "temperature": 0.5}},
    )

    assert hijo == {"llm": {"provider": "groq", "model": "propio", "temperature": 0.5}}


def test_sin_inherit_el_bloque_queda_tal_cual() -> None:
    hijo = resolver_inherit({"llm": {"model": "propio"}}, {"llm": {"provider": "groq"}})

    assert hijo == {"llm": {"model": "propio"}}


def test_inherit_false_tambien_strippea_la_clave() -> None:
    """``inherit`` es instrucción de merge: nunca debe llegar a un modelo."""
    hijo = resolver_inherit({"llm": {"inherit": False, "model": "x"}}, {"llm": {"model": "y"}})

    assert hijo == {"llm": {"model": "x"}}


def test_inherit_contra_un_padre_sin_ese_bloque() -> None:
    assert resolver_inherit({"llm": {"inherit": True, "model": "x"}}, {}) == {"llm": {"model": "x"}}


def test_inherit_contra_un_bloque_del_padre_que_no_es_dict() -> None:
    assert resolver_inherit({"llm": {"inherit": True}}, {"llm": "roto"}) == {"llm": {}}


def test_los_valores_no_dict_del_hijo_pasan_intactos() -> None:
    assert resolver_inherit({"id": "sub", "tools": ["a"]}, {"id": "padre"}) == {
        "id": "sub",
        "tools": ["a"],
    }
