"""Bloques ``delegation`` (global) y ``AgentDelegationConfig`` (per-agente).

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from inaki.config.schema._base import _ConfigBaseModel


class DelegationConfig(_ConfigBaseModel):
    """Config global de delegación (aplica a todos los agentes como valores por defecto)."""

    max_iterations_per_sub: int = 10
    """Vueltas máximas del tool loop que puede gastar UNA llamada delegada.

    Equivalente de ``tools.tool_call_max_iterations`` para el turno one-shot del
    sub-agente, y con default más generoso (10 vs 5): al sub se le delega una
    tarea completa, no un intercambio conversacional."""

    timeout_seconds: int = 60
    """Presupuesto de reloj de una llamada delegada, en segundos.

    Se aplica como ``asyncio.wait_for`` sobre el turno del sub-agente: al
    vencerse, la delegación se corta y el caller recibe el timeout como
    resultado. Es un techo de tiempo real, independiente de
    ``max_iterations_per_sub``, que cuenta vueltas."""


class AgentDelegationConfig(_ConfigBaseModel):
    """Config de delegación por agente."""

    enabled: bool = False
    """Habilita la tool ``delegate`` para ESTE agente. Opt-in.

    Con ``False`` (default) la tool no se registra siquiera: no aparece en los
    schemas y el modelo no puede razonar sobre ella. Encenderlo también inyecta
    al system prompt el bloque de descubrimiento con los agentes disponibles y
    sus tools."""

    allowed_targets: list[str] = []
    """Allow-list de sub-agentes a los que delegar. Lista vacía = todos los disponibles.

    Se INTERSECA con los sub-agentes registrados (``agents/sub-agents/*.yaml``):
    nunca amplía el universo, solo lo recorta — un id que no existe se ignora.
    Filtra a la vez los destinos que la tool acepta y los que se anuncian en el
    bloque de descubrimiento del prompt. Si la intersección queda vacía, la tool
    ``delegate`` no se registra."""
