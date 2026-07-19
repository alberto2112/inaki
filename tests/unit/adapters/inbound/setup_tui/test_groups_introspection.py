"""Introspección de la política de respuesta en grupos para la TUI (v3).

Tras la migración ``groups-vs-broadcast``, los campos ``behavior``,
``bot_username``, ``rate_limiter`` y ``rate_limiter_window`` viven en
``TelegramGroupsConfig`` (antes en ``BroadcastConfig``). El árbol de schema
(``build_schema_tree``) debe verlos bajo groups y NUNCA bajo broadcast. Estos
tests son el guard de esa garantía — si alguien re-mezcla los campos, fallan.
"""

from __future__ import annotations

from adapters.inbound.setup_tui._schema_tree import build_schema_tree
from adapters.inbound.setup_tui.domain.schema_node import SchemaNode
from infrastructure.config import BroadcastConfig, TelegramGroupsConfig

_POLITICA = {"behavior", "bot_username", "rate_limiter", "rate_limiter_window"}


def _leaf(node: SchemaNode, key: str) -> SchemaNode:
    return next(c for c in node.children if c.key == key)


def test_groups_expone_politica_de_respuesta():
    """Con valores presentes, el árbol ve los 4 campos de política; behavior es
    un enum con las 3 opciones."""
    valores = {
        "behavior": "autonomous",
        "bot_username": "bot",
        "rate_limiter": 5,
        "rate_limiter_window": 30,
    }
    tree = build_schema_tree(TelegramGroupsConfig, valores, root_label="groups")
    presentes = {c.key for c in tree.children if not c.is_section}
    assert _POLITICA <= presentes

    behavior = _leaf(tree, "behavior")
    assert behavior.field is not None
    assert behavior.field.kind == "enum"
    assert set(behavior.field.enum_choices or ()) == {"listen", "mention", "autonomous"}


def test_groups_politica_aparece_como_addable_si_ausente():
    """Sin valores, la política se ofrece como addable (regla 'solo lo presente')."""
    tree = build_schema_tree(TelegramGroupsConfig, {}, root_label="groups")
    assert _POLITICA <= {o.key for o in tree.addable}


def test_broadcast_ya_no_expone_politica_de_grupos():
    """Guard de regresión: BroadcastConfig no declara la política de grupos —
    ni como campo presente ni como opción añadible."""
    tree = build_schema_tree(BroadcastConfig, {"server": {"port": 6499}}, root_label="broadcast")
    declarados = {c.key for c in tree.children} | {o.key for o in tree.addable}
    assert _POLITICA.isdisjoint(declarados)
