"""Tests de la lógica pura del árbol de navegación (TUI v3).

No se monta Textual (mismo criterio que ``test_base_page_helpers.py``): se
verifican las derivaciones del ``SchemaNode`` (``leaf_children``, ``iter_sections``,
``breadcrumb_parts``) y el helper ``_find_node`` de ``TreeEditorPage`` construyendo
la página con ``__new__``.
"""

from __future__ import annotations

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.domain.schema_node import (
    SchemaNode,
    breadcrumb_parts,
    iter_sections,
)
from adapters.inbound.setup_tui.screens._tree_editor import TreeEditorPage


def _leaf(path: tuple[str, ...]) -> SchemaNode:
    return SchemaNode(
        path=path,
        label=path[-1],
        is_section=False,
        field=Field(label=path[-1], value="x", kind="scalar"),
    )


def _tree() -> SchemaNode:
    """Árbol: root(id, name, channels(telegram(token, groups(behavior))))."""
    behavior = _leaf(("channels", "telegram", "groups", "behavior"))
    groups = SchemaNode(
        path=("channels", "telegram", "groups"),
        label="groups",
        is_section=True,
        children=[behavior],
    )
    token = _leaf(("channels", "telegram", "token"))
    telegram = SchemaNode(
        path=("channels", "telegram"),
        label="telegram",
        is_section=True,
        children=[token, groups],
    )
    channels = SchemaNode(
        path=("channels",), label="channels", is_section=True, children=[telegram]
    )
    return SchemaNode(
        path=(),
        label="anacleto",
        is_section=True,
        children=[_leaf(("id",)), _leaf(("name",)), channels],
    )


def test_leaf_y_section_children_se_separan():
    root = _tree()
    assert [n.key for n in root.leaf_children] == ["id", "name"]
    assert [n.key for n in root.section_children] == ["channels"]


def test_depth_y_key():
    root = _tree()
    groups = root.section_children[0].section_children[0].section_children[0]
    assert groups.key == "groups"
    assert groups.depth == 3
    assert root.depth == 0
    assert root.key == ""


def test_iter_sections_dfs_incluye_raiz():
    root = _tree()
    labels = [s.label for s in iter_sections(root)]
    # raíz primero, luego DFS de secciones (no incluye hojas)
    assert labels == ["anacleto", "channels", "telegram", "groups"]


def test_breadcrumb_parts():
    root = _tree()
    groups = next(s for s in iter_sections(root) if s.key == "groups")
    assert breadcrumb_parts(groups, "anacleto") == [
        "anacleto",
        "channels",
        "telegram",
        "groups",
    ]
    assert breadcrumb_parts(root, "anacleto") == ["anacleto"]


def test_find_node_localiza_por_path():
    page = TreeEditorPage.__new__(TreeEditorPage)
    root = _tree()
    encontrado = page._find_node(root, ("channels", "telegram", "groups"))
    assert encontrado is not None
    assert encontrado.label == "groups"


def test_find_node_devuelve_none_si_no_existe():
    page = TreeEditorPage.__new__(TreeEditorPage)
    root = _tree()
    assert page._find_node(root, ("channels", "slack")) is None


def test_find_node_encuentra_la_raiz():
    page = TreeEditorPage.__new__(TreeEditorPage)
    root = _tree()
    assert page._find_node(root, ()) is root
