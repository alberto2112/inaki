"""Pantalla de override de ``delegation`` para un agente."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class AgentDelegationScreen(SectionEditorScreen):
    """Override de la sección ``delegation`` en la capa del agente."""

    SECTION_KEY = "delegation"
    TITULO = "Delegation — Override de agente"
    CAMPOS = (
        FieldSpec(
            "enabled",
            bool,
            "Activar la delegación para este agente",
            placeholder="false",
        ),
        FieldSpec(
            "allowed_targets",
            str,
            "Agentes destino permitidos",
            placeholder="agente1, agente2, agente3",
            es_lista=True,
        ),
    )
