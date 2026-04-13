"""
run_tool_loop — helper compartido para el loop de tool calls.

Delegation-agnostic: no sabe nada de depth, recursión ni delegación.
Solo ejecuta el loop LLM ↔ tools hasta obtener respuesta final o
alcanzar el límite de iteraciones.

Usado por:
- RunAgentUseCase (conversational)
- RunAgentOneShotUseCase (one-shot / delegation child)
"""

from __future__ import annotations

import json
import logging

from core.domain.entities.message import Message, Role
from core.domain.errors import ToolLoopMaxIterationsError
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.tool_port import IToolExecutor

logger = logging.getLogger(__name__)


def _extract_tool_calls(raw: str) -> tuple[list[dict], str | None]:
    """Extrae tool calls del JSON devuelto por el LLM.

    Returns:
        (tool_calls, error): tool_calls si el parseo fue exitoso,
        o ([], error_message) si el JSON es malformado para que el LLM
        pueda corregirse.
    """
    stripped = raw.strip()
    if not stripped.startswith("{"):
        return [], None
    try:
        data = json.loads(stripped)
        return data.get("tool_calls", []), None
    except json.JSONDecodeError as exc:
        return [], (
            f"Tu respuesta parece un tool call pero el JSON es inválido: {exc}. "
            "Asegurate de que el campo 'arguments' sea un string con JSON escapado, "
            'por ejemplo: "arguments": "{\\"key\\": \\"value\\"}". '
            "Intentalo de nuevo con JSON válido."
        )


async def run_tool_loop(
    *,
    llm: ILLMProvider,
    tools: IToolExecutor,
    messages: list[Message],
    system_prompt: str,
    tool_schemas: list[dict],
    max_iterations: int,
    circuit_breaker_threshold: int,
    agent_id: str,
) -> str:
    """
    Ejecuta el loop LLM + tool-dispatch hasta obtener respuesta final o
    alcanzar `max_iterations`.

    Args:
        llm: Proveedor LLM (ILLMProvider).
        tools: Ejecutor de tools (IToolExecutor).
        messages: Historial de mensajes de entrada (no se muta el original).
        system_prompt: Prompt de sistema a pasar al LLM.
        tool_schemas: Schemas de tools disponibles para el LLM.
        max_iterations: Límite de iteraciones del loop.
        circuit_breaker_threshold: Número de fallos de una tool antes de abrir el circuit breaker.
        agent_id: ID del agente (solo para logging).

    Returns:
        El texto de respuesta final del LLM (sin tool calls).

    Raises:
        ToolLoopMaxIterationsError: Si se alcanzan `max_iterations` sin obtener
            respuesta final. El atributo `.last_response` contiene el último texto
            del LLM en ese momento.
    """
    working_messages = list(messages)
    failure_counts: dict[str, int] = {}
    tripped: set[str] = set()
    last_raw: str = ""

    for iteration in range(max_iterations):
        raw = await llm.complete(
            working_messages,
            system_prompt,
            tools=tool_schemas if tool_schemas else None,
        )
        last_raw = raw

        tool_calls, parse_error = _extract_tool_calls(raw)
        if parse_error:
            logger.warning("Tool call JSON malformado del LLM: %s", parse_error)
            working_messages.append(
                Message(role=Role.USER, content=f"[Error de formato]\n{parse_error}")
            )
            continue
        if not tool_calls:
            return raw

        # Agregar el mensaje del assistant con los tool calls al historial
        # para que el LLM sepa que ÉL pidió ejecutar estas tools.
        working_messages.append(
            Message(role=Role.ASSISTANT, content="", tool_calls=tool_calls)
        )

        for tc in tool_calls:
            tc_id = tc.get("id", "")
            tool_name = tc.get("function", {}).get("name", "")
            args_raw = tc.get("function", {}).get("arguments", "{}")
            try:
                kwargs = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                kwargs = {}

            if tool_name in tripped:
                logger.warning(
                    "Circuit breaker abierto para '%s' — llamada bloqueada", tool_name
                )
                working_messages.append(Message(
                    role=Role.TOOL,
                    content=(
                        f"CIRCUIT OPEN — esta tool ya falló "
                        f"{circuit_breaker_threshold} vez/veces en este turno. "
                        "NO la vuelvas a llamar. Respondé al usuario con lo que "
                        "sabés, o pedile ayuda para resolver el bloqueo."
                    ),
                    tool_call_id=tc_id,
                ))
                continue

            result = await tools.execute(tool_name, **kwargs)
            working_messages.append(Message(
                role=Role.TOOL,
                content=result.output,
                tool_call_id=tc_id,
            ))
            logger.debug("Tool '%s' ejecutada: success=%s", tool_name, result.success)

            if result.success:
                failure_counts[tool_name] = 0
            elif not result.retryable:
                failure_counts[tool_name] = failure_counts.get(tool_name, 0) + 1
                if failure_counts[tool_name] >= circuit_breaker_threshold:
                    tripped.add(tool_name)
                    logger.warning(
                        "Circuit breaker DISPARADO para '%s' tras %d fallos no-retryable",
                        tool_name,
                        failure_counts[tool_name],
                    )

    logger.warning("Máximo de iteraciones de tool calls alcanzado para '%s'", agent_id)
    raise ToolLoopMaxIterationsError(last_response=last_raw)
