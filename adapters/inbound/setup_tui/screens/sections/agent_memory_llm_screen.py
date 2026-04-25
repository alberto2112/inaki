"""
Pantalla de override de ``memory.llm`` para un agente.

Los 4 campos usan ``TristateToggle`` (3 estados: Heredar / Valor propio / null explícito).
Este es el único sub-screen donde ``es_tristate=True`` se aplica en modo override.
"""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen
from adapters.inbound.setup_tui.widgets.diff_preview import DiffPreview
from core.ports.config_repository import LayerName
from core.use_cases.config.update_agent_layer import CampoTriestado, TristadoValor
from adapters.inbound.setup_tui.widgets.tristate_toggle import TristateToggle, TristateValorUI


class AgentMemoryLLMScreen(SectionEditorScreen):
    """
    Override de la sub-sección ``memory.llm`` en la capa del agente.

    Los 4 campos usan TristateToggle porque distinguen:
      - INHERIT       → ausente del YAML del agente (hereda del LLM base).
      - OVERRIDE_VALUE → escribe el valor.
      - OVERRIDE_NULL  → escribe ``null`` explícito (resetea al None).
    """

    SECTION_KEY = "llm"
    TITULO = "Memory LLM — Override de agente (tri-estado)"
    CAMPOS = (
        FieldSpec(
            "model",
            str,
            "Override del modelo para consolidación de memoria",
            placeholder="",
            es_tristate=True,
        ),
        FieldSpec(
            "provider",
            str,
            "Override del provider para consolidación",
            dropdown_source="providers",
            placeholder="",
            es_tristate=True,
        ),
        FieldSpec(
            "temperature",
            float,
            "Override de la temperatura para consolidación",
            placeholder="",
            es_tristate=True,
        ),
        FieldSpec(
            "max_tokens",
            int,
            "Override del máximo de tokens para consolidación",
            placeholder="",
            es_tristate=True,
        ),
    )

    def _cargar(self) -> None:
        """Lee memory.llm de la capa del agente."""
        datos_capa = self._container.repo.read_layer(
            self._layer, agent_id=self._agent_id
        )
        memory_datos = datos_capa.get("memory") or {}
        self._valores_capa = (memory_datos.get("llm") or {}).copy()
        self._yaml_antes = self._container.repo.render_yaml(
            {"memory": {"llm": self._valores_capa}}
        )
        self._poblar_campos()

    async def _guardar(self) -> None:
        """Guarda memory.llm con tri-estado."""
        cambios_seccion = self._recopilar_cambios()
        if not cambios_seccion:
            self.notify("Sin cambios para guardar.", title="Info")
            return

        cambios = {"memory": {"llm": cambios_seccion}}

        # Diff preview — solo muestra valores resueltos (no CampoTriestado)
        preview_vals: dict = {}
        for k, v in cambios_seccion.items():
            if isinstance(v, CampoTriestado):
                if v.modo == TristadoValor.INHERIT:
                    continue
                elif v.modo == TristadoValor.OVERRIDE_NULL:
                    preview_vals[k] = None
                else:
                    preview_vals[k] = v.valor
            else:
                preview_vals[k] = v

        yaml_despues = self._container.repo.render_yaml(
            {"memory": {"llm": {**self._valores_capa, **preview_vals}}}
        )
        diff_widget = self.query_one("#diff-preview", DiffPreview)
        diff_widget.actualizar(self._yaml_antes, yaml_despues, etiqueta="memory.llm")

        try:
            self._container.update_agent_layer.execute(
                agent_id=self._agent_id or "",
                cambios=cambios,
                layer=self._layer,
            )
            self._yaml_antes = yaml_despues
            self.notify("Sección 'memory.llm' guardada.", title="OK")
        except Exception as e:
            self.notify(f"Error al guardar: {e}", title="Error", severity="error")
