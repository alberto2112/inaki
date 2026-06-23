"""TreeEditorPage — página base split-pane (árbol de secciones + detalle editable).

Layout (TUI v3):
  - ``TopBar`` arriba (breadcrumb).
  - ``Horizontal``: ``Tree`` (izq, secciones presentes) + ``VerticalScroll`` (der,
    panel del nodo seleccionado).
  - ``StatusBar`` abajo.

Regla central: **solo se pinta lo presente en el YAML**. El árbol cuelga las
sub-secciones; el panel lista los campos hoja de la sección actual Y las
opciones añadibles (``+ nombre``) — TODO en una sola lista navegable y accionable.

Interacción:
  - Foco en el árbol: ``↑↓`` navega secciones (repuebla el panel), ``Enter`` baja
    el foco al panel.
  - Foco en el panel: ``↑↓`` navega los ítems, ``Enter`` edita un campo presente o
    AÑADE una opción ``+``; ``Esc`` vuelve al árbol.
  - ``a`` abre el modal de añadir de la sección; ``d`` elimina (campo en el panel,
    sección en el árbol) con confirmación.

Las subclases implementan los hooks de persistencia (``reload_root``,
``persist_field_saved``, ``persist_tristate_saved``, ``persist_add``,
``persist_delete``) — esta base no conoce capas YAML ni el repo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, Tree

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.domain.schema_node import SchemaNode, breadcrumb_parts
from adapters.inbound.setup_tui.widgets.detail_row import (
    DetailRow,
    field_value_class,
    field_value_markup,
)
from adapters.inbound.setup_tui.widgets.status_bar import StatusBar
from adapters.inbound.setup_tui.widgets.top_bar import TopBar

if TYPE_CHECKING:
    from textual.widgets.tree import TreeNode

    from adapters.inbound.setup_tui.domain.schema_node import AddableOption
    from adapters.inbound.setup_tui.modals.tristate import TristateResult

FocusZone = Literal["tree", "detail"]
# Un ítem del panel: ("field", leaf SchemaNode) o ("add", AddableOption).
DetailItem = tuple[Literal["field", "add"], object]


class TreeEditorPage(Screen):
    """Base de las páginas de edición v3 (split-pane). Ver docstring del módulo."""

    DEFAULT_CSS = """
    TreeEditorPage #split {
        height: 1fr;
        padding: 1 1 0 1;
    }
    TreeEditorPage #nav {
        width: 38;
        border: round $primary-darken-1;
        border-title-color: $accent;
        background: #0d0d0d;
        padding: 0 1;
        margin: 0 1 0 0;
    }
    TreeEditorPage #nav:focus-within {
        border: round $accent;
    }
    TreeEditorPage #detail {
        width: 1fr;
        border: round $primary-darken-1;
        padding: 0 1;
    }
    TreeEditorPage #detail .crumb {
        color: $text-muted;
        height: 1;
        margin: 0 0 1 0;
    }
    TreeEditorPage #detail .title {
        color: $accent;
        text-style: bold;
        height: 1;
    }
    TreeEditorPage #detail .empty {
        color: $text-muted;
        text-style: italic;
    }
    TreeEditorPage Tree {
        background: transparent;
    }
    TreeEditorPage Tree > .tree--cursor {
        background: $accent 35%;
        color: $text;
        text-style: bold;
    }
    TreeEditorPage Tree:focus > .tree--cursor {
        background: $accent;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("up", "cursor_up", show=False, priority=True),
        Binding("k", "cursor_up", show=False, priority=True),
        Binding("down", "cursor_down", show=False, priority=True),
        Binding("j", "cursor_down", show=False, priority=True),
        # priority para que el widget Tree (que tiene el foco) no se los coma.
        Binding("enter", "enter", show=False, priority=True),
        Binding("right", "enter", show=False, priority=True),
        Binding("a", "add", show=False, priority=True),
        Binding("d", "delete", show=False, priority=True),
        Binding("escape", "back", show=False, priority=True),
    ]

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._root_node: SchemaNode | None = None
        self._current_section: SchemaNode | None = None
        self._focus_zone: FocusZone = "tree"
        self._detail_items: list[DetailItem] = []
        self._detail_rows: list[DetailRow] = []
        self._detail_cursor: int = 0
        self._pending_add: SchemaNode | None = None
        self._pending_delete: SchemaNode | None = None

    # ------------------------------------------------------------------
    # Hooks que las subclases DEBEN implementar
    # ------------------------------------------------------------------

    def root_label(self) -> str:
        raise NotImplementedError

    def reload_root(self) -> SchemaNode:
        raise NotImplementedError

    def persist_field_saved(self, leaf: SchemaNode, field: Field) -> None:
        raise NotImplementedError

    def persist_tristate_saved(
        self, leaf: SchemaNode, field: Field, result: "TristateResult"
    ) -> None:
        raise NotImplementedError

    def persist_add(self, parent: SchemaNode, option: "AddableOption") -> None:
        raise NotImplementedError

    def persist_delete(self, node: SchemaNode) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # compose / mount
    # ------------------------------------------------------------------

    def breadcrumb(self) -> str:
        return "inaki / config"

    def compose(self) -> ComposeResult:
        yield TopBar(self.breadcrumb())
        with Horizontal(id="split"):
            yield Tree(self.root_label() if self._safe_label() else "config", id="nav")
            yield VerticalScroll(id="detail")
        yield StatusBar(
            "[bold]↑↓[/bold] [dim]navegar[/dim]   "
            "[bold]enter[/bold] [dim]editar/abrir[/dim]   "
            "[bold]a[/bold] [dim]añadir[/dim]   "
            "[bold]d[/bold] [dim]eliminar[/dim]   "
            "[bold]esc[/bold] [dim]volver[/dim]   "
            "[bold]q[/bold] [dim]salir[/dim]"
        )

    def _safe_label(self) -> bool:
        try:
            self.root_label()
            return True
        except NotImplementedError:
            return False

    async def on_mount(self) -> None:
        self._root_node = self.reload_root()
        tree = self.query_one("#nav", Tree)
        tree.show_guides = True
        tree.root.data = self._root_node
        tree.root.set_label(self._root_node.label)
        self._populate_tree(tree.root, self._root_node)
        tree.root.expand_all()
        tree.focus()
        await self._render_detail(self._root_node)

    def _populate_tree(self, tree_node: "TreeNode", schema_node: SchemaNode) -> None:
        for sec in schema_node.section_children:
            child = tree_node.add(sec.label, data=sec)
            self._populate_tree(child, sec)

    # ------------------------------------------------------------------
    # Árbol: selección → repoblar panel
    # ------------------------------------------------------------------

    async def on_tree_node_highlighted(self, event: "Tree.NodeHighlighted") -> None:
        node = event.node.data
        if isinstance(node, SchemaNode):
            await self._render_detail(node)

    async def _render_detail(self, section: SchemaNode) -> None:
        """Repuebla el panel: campos presentes (editables) + addables (``+``)."""
        self._current_section = section
        self._detail_items = []
        self._detail_rows = []
        self._detail_cursor = 0

        detail = self.query_one("#detail", VerticalScroll)
        await detail.remove_children()

        crumb = " › ".join(breadcrumb_parts(section, self._root_label_or_empty()))
        await detail.mount(Label(crumb, classes="crumb"))
        await detail.mount(Label(section.label, classes="title"))

        rows: list[DetailRow] = []
        for leaf in section.leaf_children:
            if leaf.field is None:
                continue
            markup, muted = field_value_markup(leaf.field)
            row = DetailRow(
                key=leaf.label,
                value_markup=markup,
                is_add=False,
                muted=muted,
                value_class=field_value_class(leaf.field),
            )
            self._detail_items.append(("field", leaf))
            rows.append(row)

        for opt in section.addable:
            hint = "sección" if opt.is_section else _opt_hint(opt)
            row = DetailRow(key=f"+ {opt.label}", value_markup=hint, is_add=True, muted=True)
            self._detail_items.append(("add", opt))
            rows.append(row)

        if not rows:
            await detail.mount(Label("  (sección contenedora — elegí una subsección)", classes="empty"))
        for row in rows:
            await detail.mount(row)
        self._detail_rows = rows
        self._refresh_detail_selection()

    def _root_label_or_empty(self) -> str:
        return self._root_node.label if self._root_node else ""

    # ------------------------------------------------------------------
    # Cursor del panel
    # ------------------------------------------------------------------

    def _refresh_detail_selection(self) -> None:
        active = self._focus_zone == "detail"
        for i, row in enumerate(self._detail_rows):
            row.selected = active and i == self._detail_cursor
        if active and self._detail_rows and 0 <= self._detail_cursor < len(self._detail_rows):
            self._detail_rows[self._detail_cursor].scroll_visible(animate=False)

    def action_cursor_up(self) -> None:
        if self._focus_zone == "tree":
            self.query_one("#nav", Tree).action_cursor_up()
            return
        if self._detail_cursor > 0:
            self._detail_cursor -= 1
            self._refresh_detail_selection()

    def action_cursor_down(self) -> None:
        if self._focus_zone == "tree":
            self.query_one("#nav", Tree).action_cursor_down()
            return
        if self._detail_cursor < len(self._detail_rows) - 1:
            self._detail_cursor += 1
            self._refresh_detail_selection()

    # ------------------------------------------------------------------
    # Enter: bajar al panel (desde árbol) o accionar el ítem (en panel)
    # ------------------------------------------------------------------

    def action_enter(self) -> None:
        if self._focus_zone == "tree":
            if self._detail_rows:
                self._focus_zone = "detail"
                self._detail_cursor = 0
                self._refresh_detail_selection()
            return
        self._act_current_item()

    def action_back(self) -> None:
        if self._focus_zone == "detail":
            self._focus_zone = "tree"
            self._refresh_detail_selection()
            self.query_one("#nav", Tree).focus()
            return
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def _current_item(self) -> DetailItem | None:
        if 0 <= self._detail_cursor < len(self._detail_items):
            return self._detail_items[self._detail_cursor]
        return None

    def _act_current_item(self) -> None:
        item = self._current_item()
        if item is None:
            return
        kind, payload = item
        if kind == "field":
            self._edit_leaf(payload)  # type: ignore[arg-type]
        else:
            self._add_option(payload)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Edición de un campo (reusa los modales existentes)
    # ------------------------------------------------------------------

    def _edit_leaf(self, leaf: SchemaNode) -> None:
        if leaf.field is None:
            return
        field = leaf.field
        self._editing = leaf  # type: ignore[attr-defined]

        if field.is_tristate:
            from adapters.inbound.setup_tui.modals.tristate import EditTristateModal

            self.app.push_screen(EditTristateModal(field), self._after_tristate_edit)
            return

        from adapters.inbound.setup_tui.modals.bool import EditBoolModal
        from adapters.inbound.setup_tui.modals.enum import EditEnumModal
        from adapters.inbound.setup_tui.modals.list import EditListModal
        from adapters.inbound.setup_tui.modals.long import EditLongModal
        from adapters.inbound.setup_tui.modals.scalar import EditScalarModal
        from adapters.inbound.setup_tui.modals.secret import EditSecretModal

        if field.kind == "bool":
            self.app.push_screen(EditBoolModal(field), self._after_bool_edit)
        elif field.kind == "enum":
            self.app.push_screen(EditEnumModal(field), self._after_edit)
        elif field.kind == "list":
            self.app.push_screen(EditListModal(field), self._after_list_edit)
        elif field.kind == "long":
            self.app.push_screen(EditLongModal(field), self._after_edit)
        elif field.kind == "secret":
            self.app.push_screen(EditSecretModal(field), self._after_edit)
        else:
            self.app.push_screen(EditScalarModal(field), self._after_edit)

    def _editing_leaf(self) -> SchemaNode | None:
        return getattr(self, "_editing", None)

    def _after_edit(self, result: str | None) -> None:
        if result is None:
            return
        leaf = self._editing_leaf()
        if leaf is None or leaf.field is None:
            return
        leaf.field.value = None if result.strip() == "<null>" else result
        self._refresh_current_row(leaf.field)
        self.persist_field_saved(leaf, leaf.field)
        self._notify_daemon_restart_needed()

    def _after_bool_edit(self, result: bool | None) -> None:
        if result is None:
            return
        leaf = self._editing_leaf()
        if leaf is None or leaf.field is None:
            return
        leaf.field.value = result
        self._refresh_current_row(leaf.field)
        self.persist_field_saved(leaf, leaf.field)
        self._notify_daemon_restart_needed()

    def _after_list_edit(self, result: "list | None") -> None:
        if result is None:
            return
        leaf = self._editing_leaf()
        if leaf is None or leaf.field is None:
            return
        leaf.field.value = result
        self._refresh_current_row(leaf.field)
        self.persist_field_saved(leaf, leaf.field)
        self._notify_daemon_restart_needed()

    def _after_tristate_edit(self, result: "TristateResult | None") -> None:
        if result is None:
            return
        leaf = self._editing_leaf()
        if leaf is None or leaf.field is None:
            return
        field = leaf.field
        field.tristate_state = result.mode  # type: ignore[assignment]
        if result.mode == "override_value":
            field.value = result.value or ""
        elif result.mode == "override_null":
            field.value = None
        else:
            field.value = ""
        self._refresh_current_row(field)
        self.persist_tristate_saved(leaf, field, result)
        self._notify_daemon_restart_needed()

    def _refresh_current_row(self, field: Field) -> None:
        if 0 <= self._detail_cursor < len(self._detail_rows):
            markup, muted = field_value_markup(field)
            self._detail_rows[self._detail_cursor].refresh_value(
                markup, muted, field_value_class(field)
            )

    # ------------------------------------------------------------------
    # Add / Delete
    # ------------------------------------------------------------------

    def _add_option(self, option: "AddableOption") -> None:
        """Añade directamente la opción ``+`` seleccionada en el panel."""
        section = self._current_section
        if section is None:
            return
        self.persist_add(section, option)
        self._notify_daemon_restart_needed()
        destino = section.path + (option.key,) if option.is_section else section.path
        self.call_after_refresh(self._reload_keep_panel, destino)

    async def _reload_keep_panel(self, destino: tuple[str, ...]) -> None:
        await self.reload_and_repaint(select_path=destino)
        # Quedarse en el panel tras añadir (el usuario suele querer editar el valor).
        if self._detail_rows:
            self._focus_zone = "detail"
            self._refresh_detail_selection()

    def action_add(self) -> None:
        section = self._current_section
        if section is None or not section.addable:
            self.app.notify("nada que añadir en esta sección", timeout=2)
            return
        from adapters.inbound.setup_tui.modals.add_node import AddNodeModal

        self._pending_add = section
        self.app.push_screen(
            AddNodeModal(section.addable, f"añadir en {section.label}"),
            self._after_add,
        )

    async def _after_add(self, option: "AddableOption | None") -> None:
        section = self._pending_add
        self._pending_add = None
        if option is None or section is None:
            return
        self.persist_add(section, option)
        self._notify_daemon_restart_needed()
        destino = section.path + (option.key,) if option.is_section else section.path
        await self.reload_and_repaint(select_path=destino)

    def action_delete(self) -> None:
        if self._focus_zone == "detail":
            item = self._current_item()
            if item and item[0] == "field":
                self._open_delete_modal(item[1])  # type: ignore[arg-type]
            return
        section = self._current_section
        if section is not None and section.depth > 0:
            self._open_delete_modal(section)

    def _open_delete_modal(self, node: SchemaNode) -> None:
        from adapters.inbound.setup_tui.modals.confirm_delete import ConfirmDeleteModal

        self._pending_delete = node
        campos = [c.label for c in node.leaf_children] if node.is_section else []
        titulo = ".".join(node.path) or node.label
        self.app.push_screen(
            ConfirmDeleteModal(titulo, node.is_section, campos),
            self._after_delete,
        )

    async def _after_delete(self, confirmado: bool | None) -> None:
        node = self._pending_delete
        self._pending_delete = None
        if not confirmado or node is None:
            return
        self.persist_delete(node)
        self._notify_daemon_restart_needed()
        await self.reload_and_repaint(select_path=node.path[:-1])

    async def reload_and_repaint(self, select_path: tuple[str, ...] | None = None) -> None:
        self._root_node = self.reload_root()
        tree = self.query_one("#nav", Tree)
        tree.root.remove_children()
        tree.root.data = self._root_node
        self._populate_tree(tree.root, self._root_node)
        tree.root.expand_all()
        target = self._find_node(self._root_node, select_path) if select_path else self._root_node
        await self._render_detail(target or self._root_node)

    def _find_node(self, root: SchemaNode, path: tuple[str, ...]) -> SchemaNode | None:
        if root.path == path:
            return root
        for sec in root.section_children:
            found = self._find_node(sec, path)
            if found is not None:
                return found
        return None

    # ------------------------------------------------------------------
    # Notificación post-save
    # ------------------------------------------------------------------

    def _notify_daemon_restart_needed(self) -> None:
        try:
            notify = self.app.notify
        except (AttributeError, LookupError):
            return
        notify(
            "Reiniciá el daemon: systemctl --user restart inaki",
            title="cambio guardado",
            severity="information",
            timeout=4,
        )


def _opt_hint(option: "AddableOption") -> str:
    """Pista corta para un campo añadible: la primera línea de su descripción."""
    desc = (option.description or "").strip()
    if not desc:
        return "campo"
    primera = desc.splitlines()[0]
    return primera if len(primera) <= 40 else primera[:39] + "…"
