"""GlobalPage — edición de la configuración global (global.yaml + global.secrets.yaml)."""

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

# Mapeo de section_name → clave top-level del YAML global.
# Las claves son los nombres exactos que emite ``sections_for_model``.
# Las sub-secciones anidadas (ej. MEMORY.LLM) se mapean a la misma clave
# top-level que su padre porque el repo las escribe como dicts anidados.
_SECTION_TO_YAML_KEY: dict[str, str] = {
    "APP": "app",
    "LLM": "llm",
    "EMBEDDING": "embedding",
    "MEMORY": "memory",
    "MEMORY.LLM": "memory",
    "CHAT_HISTORY": "chat_history",
    "SKILLS": "skills",
    "TOOLS": "tools",
    "SEMANTIC_ROUTING": "semantic_routing",
    "SCHEDULER": "scheduler",
    "SCHEDULER.CHANNEL_FALLBACK": "scheduler",
    "WORKSPACE": "workspace",
    "DELEGATION": "delegation",
    "ADMIN": "admin",
    "USER": "user",
    "TRANSCRIPTION": "transcription",
    "KNOWLEDGE": "knowledge",
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
        from textual.widgets import Label

        from infrastructure.config import GlobalConfig

        # Cargar valores actuales
        current: dict[str, Any] = {}
        error_msg: str | None = None
        if self._container is not None:
            try:
                efectiva = self._container.get_effective_config.execute()
                current = efectiva.datos
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"

        # Si la lectura falló (ej. YAML malformado, claves duplicadas) mostramos
        # el error en lugar de un schema con todos los campos vacíos.
        if error_msg is not None:
            yield SectionHeader("ERROR AL CARGAR CONFIG")
            yield Label(f"  [red]{error_msg}[/red]", markup=True)
            yield Label(
                "  [dim]Corregí el archivo YAML a mano y volvé a abrir esta pantalla.[/dim]",
                markup=True,
            )
            return

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

        # Determinar la capa: secrets si el kind es "secret", sino GLOBAL
        layer = (
            LayerName.GLOBAL_SECRETS if field.kind == "secret" else LayerName.GLOBAL
        )

        # build_cambios respeta secciones anidadas: MEMORY.LLM → {memory: {llm: ...}}
        cambios = build_cambios(
            section_name=section_name,
            field_name=field_name,
            value=field.value,
            section_to_yaml=_SECTION_TO_YAML_KEY,
        )

        try:
            self._container.update_global_layer.execute(cambios=cambios, layer=layer)
            self.app.notify(
                f"guardado: {field_name}",
                title="global config",
                timeout=2,
            )
            # Post-save: avisar si el cambio rompió alguna referencia cruzada
            # (default_agent, llm.provider, embedding.provider, etc.).
            self._warn_on_invalid_refs()
        except Exception as exc:
            self.app.notify(
                f"error al guardar {field_name}: {exc}",
                title="error",
                severity="error",
                timeout=4,
            )
