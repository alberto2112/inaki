"""Bloque ``skills``: selección RAG de skills.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from inaki.config.schema._base import _ConfigBaseModel


class SkillsConfig(_ConfigBaseModel):
    """Selección RAG de SKILLS: qué instrucciones se inyectan al prompt en cada turno.

    Una skill es un bloque de instrucciones en markdown. Cuando el agente tiene
    pocas, entran todas; a partir de ``semantic_routing_min_skills`` se
    seleccionan por similitud contra el embedding de la consulta del usuario, y
    ``sticky_ttl`` evita que una skill recién elegida desaparezca al turno
    siguiente porque el usuario escribió "dale".

    Bloque hermano de ``tools``, con la MISMA mecánica pero presupuesto propio: acá
    se paga en tokens de prompt, allá en schemas ofrecidos al LLM. Las políticas
    comunes a ambos pipelines viven en ``semantic_routing``.
    """

    semantic_routing_min_skills: int = 10
    """Nº de skills a partir del cual se ACTIVA el routing. Con menos o igual, entran todas.

    Es un umbral de activación, no un mínimo de resultados: con
    ``len(skills) <= min_skills`` el agente recibe el catálogo completo y no se
    calcula ningún embedding. Subirlo posterga el routing; bajarlo lo enciende
    antes."""

    semantic_routing_top_k: int = 3
    """Máximo de skills que el retrieval devuelve por turno (default más chico que en ``tools``).

    Cada skill inyecta su texto de instrucciones al system prompt, así que este
    número se paga directo en tokens: por eso el default es 3 y no 5 como el de
    tools. Las que sigan vivas por ``sticky_ttl`` se SUMAN a estas — el techo real
    del turno es mayor que ``top_k``."""

    semantic_routing_min_score: float = 0.0
    """Similitud coseno mínima para que una skill entre en la selección. ``0.0`` = sin piso.

    Con el default, ``top_k`` manda solo y siempre se devuelven las mejores
    aunque encajen poco. Subirlo hace que un turno sin skill relevante no
    arrastre ninguna."""

    sticky_ttl: int = 3
    """Turnos que una skill seleccionada sigue viva sin volver a ser elegida. ``0`` = desactivado.

    Al ser reseleccionada, el contador vuelve al valor completo; si no, decrementa
    y a cero se cae. Es lo que sostiene el hilo cuando el usuario responde con una
    frase corta que ya no matchea la skill. Con ``0`` no se guarda estado sticky y
    cada turno arranca de cero."""
