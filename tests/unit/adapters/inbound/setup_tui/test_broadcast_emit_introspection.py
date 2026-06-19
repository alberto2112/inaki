"""Introspección de BroadcastEmitConfig en la TUI (v3, árbol de schema).

Verifica que ``channels.telegram.broadcast.emit`` y sus 3 flags llegan al árbol
cuando se introspecciona ``AgentConfig`` con ``channel_schemas``. Esto es lo que
el rediseño split-pane resolvió: antes ``channels`` (dict crudo) era invisible
en el setup; el viejo test documentaba esa limitación — ahora la cubre al revés.
"""

from __future__ import annotations

from adapters.inbound.setup_tui._schema_tree import build_schema_tree
from adapters.inbound.setup_tui.domain.schema_node import SchemaNode
from infrastructure.config import AgentConfig, BroadcastConfig, TelegramChannelConfig

_EMIT_FLAGS = {"assistant_response", "user_input_voice", "user_input_photo"}
_CHANNELS = {"telegram": TelegramChannelConfig}


def _hijo(node: SchemaNode, key: str) -> SchemaNode:
    return next(c for c in node.children if c.key == key)


def test_broadcast_directo_expone_emit_con_sus_flags():
    """Introspeccionando BroadcastConfig con emit presente, el árbol ve la
    sub-sección emit y sus 3 flags como hojas."""
    tree = build_schema_tree(
        BroadcastConfig,
        {"emit": {"assistant_response": True, "user_input_voice": False, "user_input_photo": False}},
        root_label="broadcast",
    )
    emit = _hijo(tree, "emit")
    assert emit.is_section is True
    assert {c.key for c in emit.children} == _EMIT_FLAGS


def test_emit_flags_visibles_desde_agentconfig_via_channels():
    """El día-cero que el rediseño habilitó: channels.telegram.broadcast.emit
    es navegable desde AgentConfig gracias a channel_schemas (antes invisible)."""
    valores = {
        "id": "anacleto",
        "name": "Anacleto",
        "channels": {
            "telegram": {
                "broadcast": {
                    "port": 6499,
                    "emit": {"user_input_voice": True},
                }
            }
        },
    }
    tree = build_schema_tree(
        AgentConfig,
        valores,
        root_label="anacleto",
        channel_schemas=_CHANNELS,
        exclude_keys=frozenset({"providers"}),
    )
    emit = _hijo(_hijo(_hijo(_hijo(tree, "channels"), "telegram"), "broadcast"), "emit")
    assert emit.path == ("channels", "telegram", "broadcast", "emit")
    assert _hijo(emit, "user_input_voice").field.value is True  # type: ignore[union-attr]
    # Los flags no presentes se ofrecen como addable (regla 'solo lo presente').
    assert {"assistant_response", "user_input_photo"} <= {o.key for o in emit.addable}


def test_emit_defaults_legibles_via_addable():
    """Los defaults de los flags llegan como default_value en las opciones addable."""
    tree = build_schema_tree(BroadcastConfig, {"emit": {}}, root_label="broadcast")
    emit = _hijo(tree, "emit")
    by_key = {o.key: o for o in emit.addable}
    assert by_key["assistant_response"].default_value is True
    assert by_key["user_input_voice"].default_value is False
    assert by_key["user_input_photo"].default_value is False
