"""Pantalla de edición de la sub-sección ``memory.llm``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class MemoryLLMScreen(SectionEditorScreen):
    """
    Edita la sub-sección ``memory.llm`` de ``global.yaml``.

    Permite configurar un LLM distinto para la consolidación de memoria.
    Los campos ausentes se heredan del LLM principal.
    """

    SECTION_KEY = "llm"
    TITULO = "Memory LLM — Override para consolidación"
    CAMPOS = (
        FieldSpec(
            "provider",
            str,
            "Provider para consolidación (vacío = hereda de llm.provider)",
            dropdown_source="providers",
            placeholder="",
        ),
        FieldSpec(
            "model",
            str,
            "Modelo para consolidación (vacío = hereda de llm.model)",
            placeholder="",
        ),
        FieldSpec(
            "temperature",
            float,
            "Temperatura para consolidación",
            placeholder="0.7",
        ),
        FieldSpec(
            "max_tokens",
            int,
            "Max tokens para consolidación",
            placeholder="2048",
        ),
        FieldSpec(
            "reasoning_effort",
            str,
            "Esfuerzo de razonamiento (vacío = hereda)",
            placeholder="",
        ),
    )

    def _cargar(self) -> None:
        """Lee memory.llm de la capa."""
        datos_capa = self._container.repo.read_layer(
            self._layer, agent_id=self._agent_id
        )
        # Navegar hasta memory.llm
        memory_datos = datos_capa.get("memory") or {}
        self._valores_capa = (memory_datos.get("llm") or {}).copy()
        self._yaml_antes = self._container.repo.render_yaml(
            {"memory": {"llm": self._valores_capa}}
        )
        self._poblar_campos()

    async def _guardar(self) -> None:
        """Guarda en memory.llm."""
        from core.ports.config_repository import LayerName

        cambios_seccion = self._recopilar_cambios()
        if not cambios_seccion:
            self.notify("Sin cambios para guardar.", title="Info")
            return

        cambios = {"memory": {"llm": cambios_seccion}}
        yaml_despues = self._container.repo.render_yaml(
            {"memory": {"llm": {**self._valores_capa, **{
                k: v for k, v in cambios_seccion.items()
            }}}}
        )
        from adapters.inbound.setup_tui.widgets.diff_preview import DiffPreview
        diff_widget = self.query_one("#diff-preview", DiffPreview)
        diff_widget.actualizar(self._yaml_antes, yaml_despues, etiqueta="memory.llm")

        try:
            if self._layer == LayerName.GLOBAL:
                self._container.update_global_layer.execute(cambios, layer=LayerName.GLOBAL)
            else:
                self._container.update_agent_layer.execute(
                    agent_id=self._agent_id or "",
                    cambios=cambios,
                    layer=self._layer,
                )
            self._yaml_antes = yaml_despues
            self.notify("Sección 'memory.llm' guardada.", title="OK")
        except Exception as e:
            self.notify(f"Error al guardar: {e}", title="Error", severity="error")
