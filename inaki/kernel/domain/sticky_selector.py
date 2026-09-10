"""
sticky_selector — lógica pura del Sticky Union con TTL para semantic routing.

Dado el conjunto de ids seleccionados por el routing en el turno actual y los
contadores TTL vigentes del turno anterior, produce:

  - El conjunto activo (routing ∪ sticky supervivientes).
  - El nuevo mapa de contadores persistible para el próximo turno.

Regla de TTL:
  - Un id seleccionado por el routing entra o se refresca a ``ttl``.
  - Un id sticky NO re-seleccionado decrementa su contador; si llega a 0
    se elimina.

La función es pura: sin IO, sin dependencias de dominio concreto — se usa
indistintamente para skills y tools.
"""

from __future__ import annotations


def apply_sticky(
    routing_ids: set[str],
    current_ttls: dict[str, int],
    ttl: int,
) -> tuple[set[str], dict[str, int]]:
    """Aplica la política Sticky Union con TTL.

    Args:
        routing_ids: ids seleccionados por el semantic routing en el turno actual.
        current_ttls: mapa ``{id: turnos_restantes}`` del turno previo.
        ttl: número de turnos que una selección permanece sticky.

    Returns:
        ``(active_ids, new_ttls)`` donde ``active_ids`` es la unión de
        ids vivos para este turno y ``new_ttls`` el estado a persistir.
    """
    if ttl <= 0:
        # Feature deshabilitada: sin stickiness, sin estado que guardar.
        return set(routing_ids), {}

    new_ttls: dict[str, int] = {}

    # 1. Los seleccionados por el routing entran o se refrescan a TTL completo.
    for id_ in routing_ids:
        new_ttls[id_] = ttl

    # 2. Los sticky NO re-seleccionados decrementan; si llegan a 0 se dropean.
    for id_, remaining in current_ttls.items():
        if id_ in routing_ids:
            continue  # ya refrescado en paso 1
        if remaining - 1 > 0:
            new_ttls[id_] = remaining - 1

    return set(new_ttls.keys()), new_ttls
