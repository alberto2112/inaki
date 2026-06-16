"""Tests del primitivo resolve_inherit (merge de inherit por bloque, pre-pydantic)."""

from infrastructure.config_loader import resolve_inherit


def test_inherit_true_hereda_bloque_completo() -> None:
    parent = {"llm": {"provider": "openrouter", "model": "gpt-x", "temperature": 0.7}}
    child = {"llm": {"inherit": True}}

    result = resolve_inherit(child, parent)

    assert result == {"llm": {"provider": "openrouter", "model": "gpt-x", "temperature": 0.7}}


def test_inherit_true_con_override_de_campos() -> None:
    parent = {"llm": {"provider": "openrouter", "model": "gpt-x", "temperature": 0.7}}
    child = {"llm": {"inherit": True, "model": "gpt-y"}}

    result = resolve_inherit(child, parent)

    assert result == {"llm": {"provider": "openrouter", "model": "gpt-y", "temperature": 0.7}}


def test_inherit_false_no_hereda() -> None:
    parent = {"llm": {"provider": "openrouter", "model": "gpt-x"}}
    child = {"llm": {"inherit": False, "model": "gpt-z"}}

    result = resolve_inherit(child, parent)

    assert result == {"llm": {"model": "gpt-z"}}


def test_bloque_sin_inherit_no_hereda() -> None:
    parent = {"llm": {"provider": "openrouter", "model": "gpt-x"}}
    child = {"llm": {"model": "gpt-z"}}

    result = resolve_inherit(child, parent)

    assert result == {"llm": {"model": "gpt-z"}}


def test_inherit_true_con_override_anidado() -> None:
    parent = {
        "memories": {
            "consolidation": {"enabled": True, "delay_seconds": 60},
            "reconciliation": {"enabled": True},
        }
    }
    child = {
        "memories": {
            "inherit": True,
            "consolidation": {"delay_seconds": 120},
        }
    }

    result = resolve_inherit(child, parent)

    assert result == {
        "memories": {
            "consolidation": {"enabled": True, "delay_seconds": 120},
            "reconciliation": {"enabled": True},
        }
    }


def test_strip_de_inherit_siempre_se_aplica() -> None:
    parent = {"llm": {"provider": "openrouter"}}
    child = {"llm": {"inherit": True}, "channels": {"inherit": False, "telegram": {}}}

    result = resolve_inherit(child, parent)

    for block in result.values():
        assert "inherit" not in block


def test_bloques_no_dict_pasan_intactos() -> None:
    parent = {"name": "padre"}
    child = {"name": "hijo", "delegation": {"inherit": True}}

    result = resolve_inherit(child, parent)

    assert result["name"] == "hijo"
    assert result["delegation"] == {}


def test_parent_sin_el_bloque_hereda_vacio_mas_override() -> None:
    parent: dict = {}
    child = {"llm": {"inherit": True, "model": "gpt-z"}}

    result = resolve_inherit(child, parent)

    assert result == {"llm": {"model": "gpt-z"}}
