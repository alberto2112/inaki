"""GlobalPage — edición de la configuración global (global.yaml + global.secrets.yaml)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult

from adapters.inbound.setup_tui._schema import sections_for_model
from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.screens._base import BasePage
from adapters.inbound.setup_tui.widgets.config_row import ConfigRow
from adapters.inbound.setup_tui.widgets.section_header import SectionHeader

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.di import SetupContainer

# Mapeo de section_name (lower) → clave top-level del YAML global
_SECTION_TO_YAML_KEY: dict[str, str] = {
    "APP": "app",
    "LLM": "llm",
    "EMBEDDING": "embedding",
    "MEMORY": "memory",
    "CHAT_HISTORY": "chat_history",
    "SKILLS": "skills",
    "TOOLS": "tools",
    "SEMANTIC_ROUTING": "semantic_routing",
    "SCHEDULER": "scheduler",
    "WORKSPACE": "workspace",
    "DELEGATION": "delegation",
    "ADMIN": "admin",
    "USER": "user",
    "TRANSCRIPTION": "transcription",
    "KNOWLEDGE": "knowledge",
    # sub-secciones de MEMORY que el schema mapper puede emitir
    "MEMORYCONFIG": "memory",
    "SCHEDULERCONFIG": "scheduler",
    "CHANNELFALLBACKCONFIG": "scheduler",
}


class GlobalPage(BasePage):
    """Página de edición de configuración global.

    Carga el config efectivo mergeado (global.yaml + global.secrets.yaml),
    introspecciona ``GlobalConfig`` vía ``_schema.py`` para generar las secciones
    y los ``Field``, y persiste cada edición inmediatamente al repo.
    """

    def __init__(self, container: "SetupContainer | None", **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._container = container
        # Mapeo field → (section_name, field_name) para construir el cambio al guardar
        self._field_section: dict[int, tuple[str, str]] = {}

    def breadcrumb(self) -> str:
        return "inaki / config / global"

    def compose_body(self) -> ComposeResult:
        from infrastructure.config import GlobalConfig

        # Cargar valores actuales
        current: dict[str, Any] = {}
        if self._container is not None:
            try:
                efectiva = self._container.get_effective_config.execute()
                current = efectiva.datos
            except Exception:
                pass

        # Generar secciones con el schema mapper
        sections = sections_for_model(GlobalConfig, current, section_prefix="APP")

        for section_name, fields in sections:
            yield SectionHeader(section_name)
            for field in fields:
                # Registrar a qué sección pertenece para el guardado
                self._field_section[id(field)] = (section_name, field.label)
                row = ConfigRow(field)
                yield row

    def _on_field_saved(self, field: Field) -> None:
        """Persiste el cambio inmediatamente en la capa global correspondiente."""
        if self._container is None:
            return

        from core.ports.config_repository import LayerName

        section_name, field_name = self._field_section.get(id(field), ("", field.label))

        # Determinar la clave YAML top-level
        yaml_key = _SECTION_TO_YAML_KEY.get(section_name.upper(), section_name.lower())

        # Determinar la capa: secrets si el kind es "secret", sino GLOBAL
        layer = (
            LayerName.GLOBAL_SECRETS if field.kind == "secret" else LayerName.GLOBAL
        )

        cambios = {yaml_key: {field_name: field.value}}

        try:
            self._container.update_global_layer.execute(cambios=cambios, layer=layer)
            self.app.notify(
                f"guardado: {field_name}",
                title="global config",
                timeout=2,
            )
        except Exception as exc:
            self.app.notify(
                f"error al guardar {field_name}: {exc}",
                title="error",
                severity="error",
                timeout=4,
            )
