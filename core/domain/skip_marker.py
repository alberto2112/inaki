"""Marcador ``__SKIP__`` — fuente única de verdad para "el agente opta por silencio".

Un turno autónomo (participación en grupo, tarea agendada) puede decidir que NO
aporta nada en este turno respondiendo con el marcador ``__SKIP__``. La supresión
del ENVÍO sigue siendo responsabilidad de cada caller (solo él sabe CÓMO manda:
reply de Telegram, ``send_message`` del scheduler, etc.). Lo que vive acá es la
**detección** + el **literal**, antes duplicados en bot.py, group_flow.py y el
use case — para no escribir la cadena mágica en un 4º lugar al wirear el scheduler.

La detección es TOLERANTE a propósito: los LLMs no siempre cumplen "respondé
EXACTAMENTE con __SKIP__" y suelen agregar pre/post-amble. Cualquier ocurrencia
(case-insensitive, en cualquier parte de la respuesta) cuenta como skip.
"""

from __future__ import annotations

SKIP_MARKER = "__SKIP__"


def is_skip_response(response: str, marker: str | None = SKIP_MARKER) -> bool:
    """``True`` si ``response`` contiene el marcador de skip (case-insensitive).

    ``marker=None`` siempre devuelve ``False`` (skip desactivado para ese caller).
    """
    return marker is not None and marker.upper() in response.upper()
