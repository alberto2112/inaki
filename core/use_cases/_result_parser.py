"""
parse_delegation_result — extrae y valida el bloque JSON de resultado de un agente delegado.

El agente hijo emite un bloque fenced ```json ... ``` al final de su respuesta.
Esta función extrae el ÚLTIMO bloque de ese tipo, lo parsea como JSON y lo
valida como DelegationResult.

Fallback de contenido: si el JSON parseado tiene ``details`` nulo o vacío, la función
captura el texto en prosa que precede al último bloque ```json``` y lo usa como
``details``. Esto permite que el agente hijo escriba su output naturalmente en prosa
(artículos, código, planes) y lo entregue completo al padre aunque no lo haya
copiado explícitamente en el campo ``details`` del JSON.

En cualquier fallo (sin bloque, JSON inválido, campos faltantes) devuelve un
DelegationResult de error con reason="result_parse_error". NUNCA lanza.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from core.domain.value_objects.delegation_result import DelegationResult

logger = logging.getLogger(__name__)

# Regex que captura bloques ```json ... ``` (no-greedy, DOTALL)
_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)

_PARSE_ERROR_REASON = "result_parse_error"


def _extract_prose_before_last_json_block(text: str) -> str | None:
    """Retorna el texto antes del último bloque ```json```, o None si no hay prosa.

    Se usa como fallback para ``details`` cuando el agente hijo no copió su output
    en el campo ``details`` del JSON (el caso típico: escribe el artículo en prosa
    y luego añade el bloque JSON con un resumen en ``details``).
    """
    last_fence = text.rfind("```json")
    if last_fence <= 0:
        return None
    prose = text[:last_fence].strip()
    return prose if prose else None


def parse_delegation_result(text: str) -> DelegationResult:
    """
    Extrae el último bloque ```json ... ``` del texto y lo valida como DelegationResult.

    Si el campo ``details`` del JSON está ausente o vacío, se usa como fallback el
    texto en prosa que precede al bloque JSON — así el agente padre recibe siempre
    el output completo del hijo, aunque éste no lo haya copiado en ``details``.

    Args:
        text: Respuesta completa del agente hijo.

    Returns:
        DelegationResult con los datos parseados, o un DelegationResult de error
        con status="failed" y reason="result_parse_error" si algo falla.
    """
    matches = _JSON_FENCE_RE.findall(text)

    if not matches:
        logger.warning("No se encontró bloque ```json``` en la respuesta del agente hijo")
        return DelegationResult(
            status="failed",
            summary="No se encontró un bloque ```json``` en la respuesta.",
            reason=_PARSE_ERROR_REASON,
            details=text,
        )

    last_block = matches[-1]

    try:
        data = json.loads(last_block)
    except json.JSONDecodeError as exc:
        logger.warning("JSON inválido en el bloque de resultado: %s", exc)
        return DelegationResult(
            status="failed",
            summary="El bloque ```json``` contiene JSON inválido.",
            reason=_PARSE_ERROR_REASON,
            details=text,
        )

    # Fallback: si details está ausente o vacío, usar la prosa previa al bloque JSON.
    # El LLM hijo escribe el output completo en prosa; si no lo copió en details,
    # lo rescatamos aquí para que el padre lo reciba íntegro.
    # Nota: si data no es dict (ej. array, null), la ValidationError lo rechazará
    # más abajo; no intentamos el fallback en ese caso.
    if isinstance(data, dict) and not data.get("details"):
        prose = _extract_prose_before_last_json_block(text)
        if prose:
            data["details"] = prose
            logger.debug(
                "parse_delegation_result: details vacío en JSON — usando prosa previa (%d chars)",
                len(prose),
            )

    try:
        return DelegationResult.model_validate(data)
    except ValidationError as exc:
        logger.warning("El bloque JSON no cumple el esquema DelegationResult: %s", exc)
        return DelegationResult(
            status="failed",
            summary="El bloque ```json``` no cumple el esquema requerido.",
            reason=_PARSE_ERROR_REASON,
            details=text,
        )
