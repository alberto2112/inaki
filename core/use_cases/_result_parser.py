"""
parse_delegation_result — extrae y valida el bloque JSON de resultado de un agente delegado.

El agente hijo emite un bloque fenced ```json ... ``` al final de su respuesta.
Esta función extrae el ÚLTIMO bloque de ese tipo, lo parsea como JSON y lo
valida como DelegationResult.

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


def parse_delegation_result(text: str) -> DelegationResult:
    """
    Extrae el último bloque ```json ... ``` del texto y lo valida como DelegationResult.

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
