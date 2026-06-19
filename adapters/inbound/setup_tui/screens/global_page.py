"""GlobalPage — edición de la config global (global.yaml + global.secrets.yaml).

Hereda ``TreeEditorPage`` (split-pane TUI v3). El árbol cuelga las secciones
presentes en los YAML globales; el panel edita los campos hoja. Solo se pinta lo
presente. ``providers`` se excluye (tiene su propia página). ``channels`` global
es un ``BaseModel`` tipado → se introspecciona normal (sin ``channel_schemas``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from adapters.inbound.setup_tui._cambios import cambios_anidados, eliminar_en_path
from adapters.inbound.setup_tui._schema_tree import build_schema_tree
from adapters.inbound.setup_tui.domain.schema_node import SchemaNode
from adapters.inbound.setup_tui.screens._tree_editor import TreeEditorPage
from adapters.inbound.setup_tui.screens._warnings import warn_on_invalid_refs
from core.ports.config_repository import LayerName
from core.use_cases.config._merge import (
    CampoTriestado,
    TristadoValor,
    deep_merge_con_eliminaciones,
)

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer
    from adapters.inbound.setup_tui.domain.field import Field
    from adapters.inbound.setup_tui.domain.schema_node import AddableOption
    from adapters.inbound.setup_tui.modals.tristate import TristateResult

_EXCLUDE = frozenset({"providers"})


class GlobalPage(TreeEditorPage):
    """Página de edición de configuración global."""

    def __init__(self, container: "SetupContainer | None", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._container = container

    # ------------------------------------------------------------------
    # Hooks de TreeEditorPage
    # ------------------------------------------------------------------

    def root_label(self) -> str:
        return "global"

    def breadcrumb(self) -> str:
        return "inaki / config / global"

    def reload_root(self) -> SchemaNode:
        if self._container is None:
            return SchemaNode(path=(), label="global", is_section=True)
        try:
            base = self._container.repo.read_layer(LayerName.GLOBAL)
            secrets = self._container.repo.read_layer(LayerName.GLOBAL_SECRETS)
            datos = deep_merge_con_eliminaciones(base, secrets)
        except Exception:
            datos = {}
        return build_schema_tree(
            self._container.global_schema,
            datos,
            root_label="global",
            exclude_keys=_EXCLUDE,
            dynamic_enums=self._container.dynamic_enums,
        )

    def persist_field_saved(self, leaf: SchemaNode, field: "Field") -> None:
        layer = LayerName.GLOBAL_SECRETS if field.kind == "secret" else LayerName.GLOBAL
        self._aplicar(cambios_anidados(leaf.path, field.value), layer)

    def persist_tristate_saved(
        self, leaf: SchemaNode, field: "Field", result: "TristateResult"
    ) -> None:
        # La config global no declara campos tri-estado, pero respetamos el
        # contrato por si una sub-sección los introdujera en el futuro.
        if result.mode == "inherit":
            campo: Any = CampoTriestado(TristadoValor.INHERIT)
        elif result.mode == "override_null":
            campo = CampoTriestado(TristadoValor.OVERRIDE_NULL)
        else:
            campo = CampoTriestado(TristadoValor.OVERRIDE_VALOR, result.value or "")
        self._aplicar(cambios_anidados(leaf.path, campo), LayerName.GLOBAL)

    def persist_add(self, parent: SchemaNode, option: "AddableOption") -> None:
        valor: Any = {} if option.is_section else option.default_value
        layer = LayerName.GLOBAL_SECRETS if option.is_secret else LayerName.GLOBAL
        self._aplicar(cambios_anidados(parent.path + (option.key,), valor), layer)

    def persist_delete(self, node: SchemaNode) -> None:
        if self._container is None:
            return
        cambios = eliminar_en_path(node.path)
        for layer in (LayerName.GLOBAL, LayerName.GLOBAL_SECRETS):
            try:
                datos = self._container.repo.read_layer(layer)
            except Exception:
                continue
            if _existe_path(datos, node.path):
                self._aplicar(cambios, layer)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _aplicar(self, cambios: dict[str, Any], layer: LayerName) -> None:
        if self._container is None:
            return
        try:
            self._container.update_global_layer.execute(cambios=cambios, layer=layer)
            warn_on_invalid_refs(self._container, self.app.notify)
        except Exception as exc:
            self.app.notify(f"error al guardar: {exc}", title="error", severity="error", timeout=4)


def _existe_path(datos: dict[str, Any], path: tuple[str, ...]) -> bool:
    """``True`` si ``path`` está presente en el dict ``datos`` (clave a clave)."""
    cur: Any = datos
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return False
        cur = cur[k]
    return True
