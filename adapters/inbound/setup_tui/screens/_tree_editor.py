"""TreeEditorPage — página base split-pane (árbol de secciones + detalle editable).

Layout (TUI v3):
  - ``TopBar`` arriba (breadcrumb).
  - ``Horizontal``: ``Tree`` (izq, secciones presentes) + ``VerticalScroll`` (der,
    campos de la sección seleccionada).
  - ``StatusBar`` abajo.

Regla central: **solo se pinta lo presente en el YAML**. El árbol cuelga las
sub-secciones; el panel muestra los campos hoja de la sección actual. Lo que
falta vive en ``SchemaNode.addable`` y se añade con un modal (FASE 3).

Interacción:
  - Foco en el árbol: ``↑↓`` navega secciones (repuebla el panel), ``Enter`` baja
    el foco al panel.
  - Foco en el panel: ``↑↓`` navega campos, ``Enter`` edita (modal según kind),
    ``Esc`` vuelve al árbol.
  - ``a`` añadir / ``d`` eliminar son contextuales al foco (delegan en hooks que
    completan las subclases + FASE 3).

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
from textual.widgets import Tree

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.domain.schema_node import (
    SchemaNode,
    breadcrumb_parts,
)
from adapters.inbound.setup_tui.widgets.config_row import ConfigRow
from adapters.inbound.setup_tui.widgets.section_header import SectionHeader
from adapters.inbound.setup_tui.widgets.status_bar import StatusBar
from adapters.inbound.setup_tui.widgets.top_bar import TopBar

if TYPE_CHECKING:
    from textual.widgets.tree import TreeNode

    from adapters.inbound.setup_tui.domain.schema_node import AddableOption
    from adapters.inbound.setup_tui.modals.tristate import TristateResult

FocusZone = Literal["tree", "detail"]


class TreeEditorPage(Screen):
    """Base de las páginas de edición v3 (split-pane). Ver docstring del módulo."""

    DEFAULT_CSS = """
    TreeEditorPage #split {
        height: 1fr;
    }
    TreeEditorPage #nav {
        width: 34;
        border-right: solid $accent-darken-2;
        background: #0d0d0d;
        padding: 0 1;
    }
    TreeEditorPage #detail {
        width: 1fr;
        padding: 0 1;
    }
    TreeEditorPage #detail .breadcrumb {
        color: $text-muted;
        height: 1;
    }
    TreeEditorPage #detail .empty {
        color: $text-muted;
        text-style: italic;
    }
    TreeEditorPage #detail .addable {
        color: $success-darken-1;
        height: 1;
    }
    TreeEditorPage #detail .addable.-selected {
        background: $boost;
        color: $success;
    }
    """

    BINDINGS = [
        Binding("up", "cursor_up", show=False, priority=True),
        Binding("k", "cursor_up", show=False, priority=True),
        Binding("down", "cursor_down", show=False, priority=True),
        Binding("j", "cursor_down", show=False, priority=True),
        # priority para que el widget Tree (que tiene el foco) no se los coma:
        # Enter baja al panel / edita; Esc sube; a/d añaden/eliminan.
        Binding("enter", "enter", show=False, priority=True),
        Binding("a", "add", show=False, priority=True),
        Binding("d", "delete", show=False, priority=True),
        Binding("escape", "back", show=False, priority=True),
    ]

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._root_node: SchemaNode | None = None
        self._current_section: SchemaNode | None = None
        self._focus_zone: FocusZone = "tree"
        # Estado del cursor del panel de detalle
        self._detail_rows: list[ConfigRow] = []
        self._detail_cursor: int = 0
        # Mapeos id(widget) → SchemaNode para resolver el path al editar/añadir
        self._row_to_leaf: dict[int, SchemaNode] = {}
        # Nodos en juego mientras un modal add/delete está abierto
        self._pending_add: SchemaNode | None = None
        self._pending_delete: SchemaNode | None = None

    # ------------------------------------------------------------------
    # Hooks que las subclases DEBEN implementar
    # ------------------------------------------------------------------

    def root_label(self) -> str:
        """Etiqueta del nodo raíz (ej. el id del agente, o ``"global"``)."""
        raise NotImplementedError

    def reload_root(self) -> SchemaNode:
        """(Re)lee la config del repo y devuelve el ``SchemaNode`` raíz fresco.

        Se llama en el montaje y tras cada add/delete para repintar."""
        raise NotImplementedError

    def persist_field_saved(self, leaf: SchemaNode, field: Field) -> None:
        """Persiste la edición de un campo simple (no tri-estado)."""
        raise NotImplementedError

    def persist_tristate_saved(
        self, leaf: SchemaNode, field: Field, result: "TristateResult"
    ) -> None:
        """Persiste la edición de un campo tri-estado (memories.llm)."""
        raise NotImplementedError

    def persist_add(self, parent: SchemaNode, option: "AddableOption") -> None:
        """Persiste el alta de una sección/campo (crea la clave 'vacía apropiada')."""
        raise NotImplementedError

    def persist_delete(self, node: SchemaNode) -> None:
        """Persiste la baja de una sección/campo (poda la clave del YAML)."""
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
        yield StatusBar()

    def _safe_label(self) -> bool:
        try:
            self.root_label()
            return True
        except NotImplementedError:
            return False

    async def on_mount(self) -> None:
        self._root_node = self.reload_root()
        tree = self.query_one("#nav", Tree)
        tree.root.data = self._root_node
        tree.root.set_label(self._root_node.label)
        self._populate_tree(tree.root, self._root_node)
        tree.root.expand_all()
        tree.focus()
        await self._render_detail(self._root_node)

    def _populate_tree(self, tree_node: "TreeNode", schema_node: SchemaNode) -> None:
        """Cuelga recursivamente las sub-secciones presentes bajo ``tree_node``."""
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
        """Repuebla el panel derecho con los campos hoja de ``section``."""
        self._current_section = section
        self._detail_rows = []
        self._row_to_leaf = {}
        self._detail_cursor = 0

        detail = self.query_one("#detail", VerticalScroll)
        await detail.remove_children()

        from textual.widgets import Label

        crumb = " › ".join(breadcrumb_parts(section, self._root_label_or_empty()))
        await detail.mount(Label(crumb, classes="breadcrumb"))
        await detail.mount(SectionHeader(section.label.upper()))

        if not section.leaf_children and not section.addable:
            await detail.mount(Label("  (sección vacía)", classes="empty"))

        for leaf in section.leaf_children:
            if leaf.field is None:
                continue
            row = ConfigRow(leaf.field)
            self._detail_rows.append(row)
            self._row_to_leaf[id(row)] = leaf
            await detail.mount(row)

        # Zona "añadir": una línea por opción addable del schema en este nivel.
        for opt in section.addable:
            etiqueta = f"  + {opt.label}" + ("/" if opt.is_section else "")
            await detail.mount(Label(etiqueta, classes="addable"))

        self._refresh_detail_selection()

    def _root_label_or_empty(self) -> str:
        return self._root_node.label if self._root_node else ""

    # ------------------------------------------------------------------
    # Cursor del panel de detalle
    # ------------------------------------------------------------------

    def _refresh_detail_selection(self) -> None:
        for i, row in enumerate(self._detail_rows):
            row.selected = i == self._detail_cursor and self._focus_zone == "detail"
        if self._detail_rows and 0 <= self._detail_cursor < len(self._detail_rows):
            self._detail_rows[self._detail_cursor].scroll_visible(animate=False)

    def action_cursor_up(self) -> None:
        # En el árbol delegamos al widget Tree (su navegación nativa); el binding
        # priority de la página captura la flecha, así que hay que reenviarla.
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
    # Enter: bajar al panel (desde árbol) o editar (en panel)
    # ------------------------------------------------------------------

    def action_enter(self) -> None:
        if self._focus_zone == "tree":
            if self._detail_rows:
                self._focus_zone = "detail"
                self._detail_cursor = 0
                self._refresh_detail_selection()
            return
        self._edit_current_leaf()

    def action_back(self) -> None:
        # Desde el panel, Esc devuelve el foco al árbol; desde el árbol, sale.
        if self._focus_zone == "detail":
            self._focus_zone = "tree"
            self._refresh_detail_selection()
            self.query_one("#nav", Tree).focus()
            return
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    # ------------------------------------------------------------------
    # Edición de un campo (reusa los modales existentes)
    # ------------------------------------------------------------------

    def _current_leaf(self) -> SchemaNode | None:
        if not self._detail_rows or not (0 <= self._detail_cursor < len(self._detail_rows)):
            return None
        row = self._detail_rows[self._detail_cursor]
        return self._row_to_leaf.get(id(row))

    def _edit_current_leaf(self) -> None:
        leaf = self._current_leaf()
        if leaf is None or leaf.field is None:
            return
        field = leaf.field

        if field.is_tristate:
            from adapters.inbound.setup_tui.modals.tristate import EditTristateModal

            self.app.push_screen(EditTristateModal(field), self._after_tristate_edit)
            return

        from adapters.inbound.setup_tui.modals.bool import EditBoolModal
        from adapters.inbound.setup_tui.modals.enum import EditEnumModal
        from adapters.inbound.setup_tui.modals.long import EditLongModal
        from adapters.inbound.setup_tui.modals.scalar import EditScalarModal
        from adapters.inbound.setup_tui.modals.secret import EditSecretModal

        if field.kind == "bool":
            self.app.push_screen(EditBoolModal(field), self._after_bool_edit)
        elif field.kind == "enum":
            self.app.push_screen(EditEnumModal(field), self._after_edit)
        elif field.kind == "long":
            self.app.push_screen(EditLongModal(field), self._after_edit)
        elif field.kind == "secret":
            self.app.push_screen(EditSecretModal(field), self._after_edit)
        else:
            self.app.push_screen(EditScalarModal(field), self._after_edit)

    def _after_edit(self, result: str | None) -> None:
        if result is None:
            return
        leaf = self._current_leaf()
        if leaf is None or leaf.field is None:
            return
        leaf.field.value = None if result.strip() == "<null>" else result
        self._refresh_current_row()
        self.persist_field_saved(leaf, leaf.field)
        self._notify_daemon_restart_needed()

    def _after_bool_edit(self, result: bool | None) -> None:
        if result is None:
            return
        leaf = self._current_leaf()
        if leaf is None or leaf.field is None:
            return
        leaf.field.value = result
        self._refresh_current_row()
        self.persist_field_saved(leaf, leaf.field)
        self._notify_daemon_restart_needed()

    def _after_tristate_edit(self, result: "TristateResult | None") -> None:
        if result is None:
            return
        leaf = self._current_leaf()
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
        self._refresh_current_row()
        self.persist_tristate_saved(leaf, field, result)
        self._notify_daemon_restart_needed()

    def _refresh_current_row(self) -> None:
        if self._detail_rows and 0 <= self._detail_cursor < len(self._detail_rows):
            self._detail_rows[self._detail_cursor].refresh_value()

    # ------------------------------------------------------------------
    # Add / Delete — delegan en hooks + recarga (modales en FASE 3)
    # ------------------------------------------------------------------

    def action_add(self) -> None:
        """Añadir en el nodo actual. El modal de selección llega en FASE 3;
        por ahora avisa si no hay nada que añadir."""
        section = self._current_section
        if section is None or not section.addable:
            self.app.notify("nada que añadir en esta sección", timeout=2)
            return
        self._open_add_modal(section)

    def action_delete(self) -> None:
        """Eliminar la sección actual (con foco en árbol) o el campo actual
        (con foco en panel). El modal de confirmación llega en FASE 3."""
        if self._focus_zone == "detail":
            leaf = self._current_leaf()
            if leaf is not None:
                self._open_delete_modal(leaf)
            return
        section = self._current_section
        if section is not None and section.depth > 0:
            self._open_delete_modal(section)

    def _open_add_modal(self, section: SchemaNode) -> None:
        """Abre el modal de selección de addable para ``section``."""
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
        # Seleccionar la nueva sub-sección creada, o la sección donde se añadió el campo.
        destino = section.path + (option.key,) if option.is_section else section.path
        await self.reload_and_repaint(select_path=destino)

    def _open_delete_modal(self, node: SchemaNode) -> None:
        """Abre el modal de confirmación de borrado para ``node``."""
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
        # Tras podar: seleccionar el contenedor padre (sección o raíz).
        await self.reload_and_repaint(select_path=node.path[:-1])

    async def reload_and_repaint(self, select_path: tuple[str, ...] | None = None) -> None:
        """Tras un add/delete: re-lee la config y repinta árbol + panel."""
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
    # Notificación post-save (idéntica a BasePage)
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
