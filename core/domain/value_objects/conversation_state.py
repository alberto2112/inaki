"""
ConversationState — estado conversacional persistido entre turnos.

Contiene los TTL actuales de skills y tools "pegajosos" (sticky), que
sobreviven turnos aunque el RAG no los re-seleccione, hasta que su contador
llegue a 0. Esto evita que follow-ups cortos o ambiguos pierdan herramientas
relevantes al contexto conversacional.

El diccionario mapea id → turnos restantes antes de expirar.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConversationState:
    """Estado conversacional — sticky selection de skills y tools."""

    sticky_skills: dict[str, int] = field(default_factory=dict)
    sticky_tools: dict[str, int] = field(default_factory=dict)
