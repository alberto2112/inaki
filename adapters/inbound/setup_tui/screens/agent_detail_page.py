"""AgentDetailPage — edición de un agente/sub-agente (split-pane TUI v3).

Hereda ``TreeEditorPage``: el árbol cuelga las secciones presentes en
``agents/{id}.yaml`` y el panel edita los campos hoja. Solo se pinta
lo presente; añadir/eliminar secciones y campos se hace con los modales.

El dict ``channels`` se introspecciona vía ``channel_schemas`` del container
(``AgentConfig.channels`` es ``dict[str, dict]``, no tipado). ``providers`` se
excluye del árbol — tiene su propia página.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from adapters.inbound.setup_tui._cambios import cambios_anidados, eliminar_en_path
from adapters.inbound.setup_tui._schema_tree import build_schema_tree
from adapters.inbound.setup_tui.choices import resolve_choices
from adapters.inbound.setup_tui.domain.schema_node import SchemaNode
from adapters.inbound.setup_tui.screens._tree_editor import TreeEditorPage
from adapters.inbound.setup_tui.screens._warnings import warn_on_invalid_refs
from core.ports.config_repository import LayerName
from core.use_cases.config._merge import CampoTriestado, TristadoValor

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer
    from adapters.inbound.setup_tui.domain.field import Field
    from adapters.inbound.setup_tui.domain.schema_node import AddableOption
    from adapters.inbound.setup_tui.modals.tristate import TristateResult

# Sub-campos de memories.llm que usan tri-estado (heredar / valor / null).
# Paths dotted lowercase reales del schema (AgentConfig.memories.llm.*).
_TRISTATE_PATHS: frozenset[str] = frozenset(
    {
        "memories.llm.provider",
        "memories.llm.model",
        "memories.llm.temperature",
        "memories.llm.max_tokens",
        "memories.llm.reasoning_effort",
    }
)

# Secciones del schema que NO se editan en esta página (tienen su propia vista).
_EXCLUDE = frozenset({"providers"})


class AgentDetailPage(TreeEditorPage):
    """Página de edición del config de un agente específico (regular o sub-agente)."""

    def __init__(
        self,
        container: "SetupContainer | None",
        agent_id: str,
        is_sub_agent: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._container = container
        self._agent_id = agent_id
        self._is_sub_agent = is_sub_agent

    @property
    def _main_layer(self) -> LayerName:
        return LayerName.SUB_AGENT if self._is_sub_agent else LayerName.AGENT

    # ------------------------------------------------------------------
    # Hooks de TreeEditorPage
    # ------------------------------------------------------------------

    def root_label(self) -> str:
        return self._agent_id

    def breadcrumb(self) -> str:
        base = "sub-agents" if self._is_sub_agent else "agents"
        return f"inaki / config / {base} / {self._agent_id}"

    def reload_root(self) -> SchemaNode:
        if self._container is None:
            return SchemaNode(path=(), label=self._agent_id, is_section=True)
        try:
            datos = self._container.repo.read_layer(self._main_layer, agent_id=self._agent_id)
        except Exception:
            # YAML roto: árbol vacío en vez de crash (el usuario corrige a mano).
            datos = {}
        return build_schema_tree(
            self._container.agent_schema,
            datos,
            root_label=self._agent_id,
            channel_schemas=self._container.channel_schemas,
            tristate_paths=_TRISTATE_PATHS,
            exclude_keys=_EXCLUDE,
            dynamic_choices=resolve_choices(self._container.repo, datos),
        )

    def persist_field_saved(self, leaf: SchemaNode, field: "Field") -> None:
        self._aplicar(cambios_anidados(leaf.path, field.value), self._main_layer)

    def persist_tristate_saved(
        self, leaf: SchemaNode, field: "Field", result: "TristateResult"
    ) -> None:
        if result.mode == "inherit":
            campo = CampoTriestado(TristadoValor.INHERIT)
        elif result.mode == "override_null":
            campo = CampoTriestado(TristadoValor.OVERRIDE_NULL)
        else:
            campo = CampoTriestado(TristadoValor.OVERRIDE_VALOR, _coerce(field, result.value or ""))
        self._aplicar(cambios_anidados(leaf.path, campo), self._main_layer)

    def persist_add(self, parent: SchemaNode, option: "AddableOption") -> None:
        valor: Any = {} if option.is_section else option.default_value
        self._aplicar(cambios_anidados(parent.path + (option.key,), valor), self._main_layer)

    def persist_delete(self, node: SchemaNode) -> None:
        # Solo se poda si el path EXISTE: aplicar el sentinel sobre una capa que
        # no lo tiene escribiría una rama nueva con el marcador (basura no
        # serializable).
        if self._container is None:
            return
        try:
            datos = self._container.repo.read_layer(self._main_layer, agent_id=self._agent_id)
        except Exception:
            return
        if _existe_path(datos, node.path):
            self._aplicar(eliminar_en_path(node.path), self._main_layer)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _aplicar(self, cambios: dict[str, Any], layer: LayerName) -> None:
        if self._container is None:
            return
        try:
            self._container.update_agent_layer.execute(
                agent_id=self._agent_id, cambios=cambios, layer=layer
            )
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


def _coerce(field: "Field", value: str) -> Any:
    """Convierte el string del input al tipo del campo (int → float → str)."""
    if field.kind == "scalar":
        try:
            return int(value)
        except (ValueError, TypeError):
            pass
        try:
            return float(value)
        except (ValueError, TypeError):
            pass
    return value
