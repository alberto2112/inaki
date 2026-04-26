"""Pantalla de override de ``embedding`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentEmbeddingScreen(SectionEditorScreen):
    """Override de la sección ``embedding`` en la capa del agente."""

    SECTION_KEY = "embedding"
    TITULO = "Embedding — Override de agente"
    CAMPOS = (
        FieldSpec(
            "provider",
            str,
            "Override del provider de embedding",
            dropdown_source="providers",
            placeholder="",
        ),
        FieldSpec(
            "model_dirname",
            str,
            "Override del directorio del modelo ONNX",
            placeholder="",
        ),
        FieldSpec(
            "dimension",
            int,
            "Override de la dimensión (requiere recrear la DB)",
            placeholder="",
        ),
    )
