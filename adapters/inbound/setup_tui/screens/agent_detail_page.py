"""AgentDetailPage — vista detallada y edición de la config de un agente."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult

from adapters.inbound.setup_tui._cambios import build_cambios
from adapters.inbound.setup_tui._schema import sections_for_model
from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.screens._base import BasePage
from adapters.inbound.setup_tui.widgets.config_row import ConfigRow
from adapters.inbound.setup_tui.widgets.section_header import SectionHeader

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer

# Mapeo de section_name → clave top-level del YAML de agente.
# Las claves son los nombres exactos que emite ``sections_for_model``.
# Las secciones anidadas (ej. MEMORY.LLM) se mapean a la misma clave
# top-level que su padre porque el repo las escribe como dicts anidados.
_SECTION_TO_YAML_KEY: dict[str, str] = {
    # Sección raíz del AgentConfig (campos simples: id, name, description, system_prompt)
    "AGENTCONFIG": "agent",
    # Sub-secciones generadas por sections_for_model (nombre = field name UPPER)
    "LLM": "llm",
    "EMBEDDING": "embedding",
    "MEMORY": "memory",
    "MEMORY.LLM": "memory",
    "CHAT_HISTORY": "chat_history",
    "SKILLS": "skills",
    "TOOLS": "tools",
    "SEMANTIC_ROUTING": "semantic_routing",
    "WORKSPACE": "workspace",
    "DELEGATION": "delegation",
    "TRANSCRIPTION": "transcription",
}

# Rutas triestadas: campos de memory.llm que el agente puede heredar del global.
# El prefijo MEMORY.LLM coincide con el nombre de sección generado por el schema mapper.
_TRISTATE_PATHS: frozenset[str] = frozenset(
    {
        "MEMORY.LLM.provider",
        "MEMORY.LLM.model",
        "MEMORY.LLM.temperature",
        "MEMORY.LLM.max_tokens",
        "MEMORY.LLM.reasoning_effort",
    }
)

# Campos raíz del AgentConfig que se mapean directamente (sin sección contenedora)
_ROOT_SECTION_FIELDS = {"id", "name", "description", "system_prompt"}


class AgentDetailPage(BasePage):
    """Página de edición del config de un agente específico.

    Carga la capa AGENT del agente (``agents/{id}.yaml``), la introspecciona
    vía el schema mapper y permite editar campo por campo mediante modales.

    Las ediciones se persisten inmediatamente en la capa correcta:
    - Campos ``secret`` → ``LayerName.AGENT_SECRETS``
    - Resto → ``LayerName.AGENT``
    """

    def __init__(
        self,
        container: "SetupContainer | None",
        agent_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._container = container
        self._agent_id = agent_id
        # Mapeo field → (section_name, field_name) para el guardado
        self._field_section: dict[int, tuple[str, str]] = {}

    def breadcrumb(self) -> str:
        return f"inaki / config / agents / {self._agent_id}"

    def compose_body(self) -> ComposeResult:
        from textual.widgets import Label

        from infrastructure.config import AgentConfig

        current: dict[str, Any] = {}
        error_msg: str | None = None
        if self._container is not None:
            try:
                from core.ports.config_repository import LayerName

                current = self._container.repo.read_layer(
                    LayerName.AGENT, agent_id=self._agent_id
                )
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"

        if error_msg is not None:
            yield SectionHeader(f"ERROR AL CARGAR AGENT {self._agent_id}")
            yield Label(f"  [red]{error_msg}[/red]", markup=True)
            yield Label(
                "  [dim]Corregí el archivo YAML a mano y volvé a abrir esta pantalla.[/dim]",
                markup=True,
            )
            return

        # Generar secciones usando AgentConfig como schema.
        # Los campos de memory.llm se marcan como triestados para que el usuario
        # pueda elegir entre heredar del global, valor propio o null explícito.
        sections = sections_for_model(AgentConfig, current, tristate_paths=_TRISTATE_PATHS)

        for section_name, fields in sections:
            yield SectionHeader(section_name)
            for field in fields:
                self._field_section[id(field)] = (section_name, field.label)
                yield ConfigRow(field)

    def _on_field_saved(self, field: Field) -> None:
        """Persiste el cambio en la capa del agente correspondiente."""
        if self._container is None:
            return

        from core.ports.config_repository import LayerName

        section_name, field_name = self._field_section.get(id(field), ("", field.label))

        # Determinar la capa: secrets para campos secret, sino AGENT
        layer = (
            LayerName.AGENT_SECRETS if field.kind == "secret" else LayerName.AGENT
        )

        # build_cambios respeta:
        #   - root fields (id/name/description/system_prompt) → flat {field: value}
        #   - secciones anidadas (MEMORY.LLM) → {memory: {llm: {field: value}}}
        cambios: dict[str, Any] = build_cambios(
            section_name=section_name,
            field_name=field_name,
            value=field.value,
            section_to_yaml=_SECTION_TO_YAML_KEY,
            root_fields=_ROOT_SECTION_FIELDS,
        )

        try:
            self._container.update_agent_layer.execute(
                agent_id=self._agent_id,
                cambios=cambios,
                layer=layer,
            )
            self.app.notify(
                f"guardado: {field_name}",
                title=f"agente {self._agent_id}",
                timeout=2,
            )
            # Post-save: avisar si el cambio rompió alguna referencia cruzada
            self._warn_on_invalid_refs()
        except Exception as exc:
            self.app.notify(
                f"error al guardar {field_name}: {exc}",
                title="error",
                severity="error",
                timeout=4,
            )

    def _on_tristate_field_saved(self, field: Field, result: Any) -> None:
        """Persiste un campo triestado en la capa AGENT del agente.

        Traduce el ``TristateResult`` a ``CampoTriestado`` y llama a
        ``update_agent_layer.execute`` con la estructura adecuada.
        """
        if self._container is None:
            return

        from core.ports.config_repository import LayerName
        from core.use_cases.config.update_agent_layer import CampoTriestado, TristadoValor

        if result.mode == "inherit":
            campo = CampoTriestado(TristadoValor.INHERIT)
        elif result.mode == "override_null":
            campo = CampoTriestado(TristadoValor.OVERRIDE_NULL)
        else:
            # Coerción de tipo desde el string del input
            valor_tipado = self._coerce_value(field, result.value or "")
            campo = CampoTriestado(TristadoValor.OVERRIDE_VALOR, valor=valor_tipado)

        cambios: dict[str, Any] = {"memory": {"llm": {field.label: campo}}}
        try:
            self._container.update_agent_layer.execute(
                agent_id=self._agent_id,
                cambios=cambios,
                layer=LayerName.AGENT,
            )
            self.app.notify(
                f"guardado: memory.llm.{field.label}",
                title="agente",
                timeout=2,
            )
            # Post-save: avisar si el override rompió alguna referencia cruzada
            # (por ejemplo memory.llm.provider apuntando a un provider inexistente).
            self._warn_on_invalid_refs()
        except Exception as exc:
            self.app.notify(
                f"error: {exc}",
                title="error",
                severity="error",
                timeout=4,
            )

    @staticmethod
    def _coerce_value(field: Field, value: str) -> Any:
        """Intenta convertir ``value`` al tipo más adecuado según ``field.kind``.

        Orden de prueba: int → float → str original.
        """
        if field.kind == "scalar":
            # Intentar conversión numérica para campos escalares
            try:
                return int(value)
            except (ValueError, TypeError):
                pass
            try:
                return float(value)
            except (ValueError, TypeError):
                pass
        return value
