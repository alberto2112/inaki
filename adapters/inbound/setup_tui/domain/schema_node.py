"""Modelo de árbol del schema para la TUI de setup.

A diferencia de ``_schema.py`` (que aplana el schema en secciones + campos y
renderiza TODO lo declarado), este modelo representa la config como un **árbol
de nodos donde solo viven las claves PRESENTES en el YAML**. Cada nodo conoce
además qué se podría **añadir** en ese nivel (``addable``) leyendo el schema
Pydantic — así el modal de "añadir sección/campo" sabe qué ofrecer.

Es el source of truth de la TUI v3 (split-pane árbol + detalle):
  - ``SchemaNode`` → un nodo del árbol (sección contenedora u hoja editable).
  - ``AddableOption`` → una opción que el usuario puede añadir en un nodo sección.

Las hojas reusan la dataclass ``Field`` (``domain/field.py``) para que los
modales de edición existentes funcionen sin cambios.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from adapters.inbound.setup_tui.domain.field import Field


@dataclass
class AddableOption:
    """Una clave del schema que NO está presente y se puede añadir.

    Atributos:
        key: Nombre de la clave en el YAML (ej. ``"groups"``, ``"token"``).
        label: Texto a mostrar en el modal (normalmente igual a ``key``).
        is_section: ``True`` si añadir esta opción crea un contenedor (``{}``);
            ``False`` si crea un campo simple con su default.
        description: Docstring corto del campo (si el schema lo expone), para
            mostrar como ayuda en el modal. Vacío si no hay.
        default_value: Valor inicial al añadir un campo simple (el default del
            schema). Irrelevante para secciones (se crean como ``{}``).
    """

    key: str
    label: str
    is_section: bool
    description: str = ""
    default_value: Any = None


@dataclass
class SchemaNode:
    """Un nodo del árbol de configuración.

    Un nodo es **sección** (contenedor: un ``BaseModel`` anidado o un canal del
    dict ``channels``) o **hoja** (un campo simple editable con un modal).

    Atributos:
        path: Ruta de claves YAML desde la raíz, ej. ``("channels", "telegram",
            "groups")``. La raíz tiene ``path == ()``.
        label: Nombre a mostrar (última clave del path, o el label de la raíz).
        is_section: ``True`` contenedor, ``False`` hoja editable.
        present: Si la clave está presente en el YAML actual. La raíz siempre
            ``True``. Los hijos del árbol son SIEMPRE presentes (lo ausente vive
            en ``addable`` del padre, no como nodo).
        field: Solo en hojas — el ``Field`` editable que consumen los modales.
        children: Sub-nodos presentes (solo en secciones).
        addable: Opciones que se pueden añadir en este nodo (solo en secciones).
    """

    path: tuple[str, ...]
    label: str
    is_section: bool
    present: bool = True
    field: Field | None = None
    children: list[SchemaNode] = dc_field(default_factory=list)
    addable: list[AddableOption] = dc_field(default_factory=list)

    @property
    def key(self) -> str:
        """Última clave del path (``""`` para la raíz)."""
        return self.path[-1] if self.path else ""

    @property
    def leaf_children(self) -> list[SchemaNode]:
        """Hijos hoja (campos editables) — lo que puebla el panel de detalle."""
        return [c for c in self.children if not c.is_section]

    @property
    def section_children(self) -> list[SchemaNode]:
        """Hijos sección (sub-contenedores) — lo que cuelga en el árbol."""
        return [c for c in self.children if c.is_section]

    @property
    def depth(self) -> int:
        """Profundidad del nodo (0 = raíz)."""
        return len(self.path)


def iter_sections(root: SchemaNode) -> list[SchemaNode]:
    """Recorre el árbol en DFS y devuelve todas las secciones (incluida la raíz).

    El orden coincide con cómo se pinta el árbol (padre antes que hijos), así que
    sirve para mapear la navegación vertical del widget ``Tree``.
    """
    out: list[SchemaNode] = [root]
    for sec in root.section_children:
        out.extend(iter_sections(sec))
    return out


def breadcrumb_parts(node: SchemaNode, root_label: str) -> list[str]:
    """Partes del breadcrumb de un nodo: ``[root_label, *path]``.

    Ej: nodo ``("channels","telegram","groups")`` con root ``"anacleto"`` →
    ``["anacleto", "channels", "telegram", "groups"]``.
    """
    return [root_label, *node.path]
