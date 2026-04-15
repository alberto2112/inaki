"""
run_tool_loop — helper compartido para el loop de tool calls.

Delegation-agnostic: no sabe nada de depth, recursión ni delegación.
Solo ejecuta el loop LLM ↔ tools hasta obtener respuesta final o
alcanzar el límite de iteraciones.

Usado por:
- RunAgentUseCase (conversational)
- RunAgentOneShotUseCase (one-shot / delegation child)

Contrato con el provider:
- ``ILLMProvider.complete()`` devuelve ``LLMResponse`` con ``text_blocks``
  y ``tool_calls`` separados. Si ``tool_calls`` está vacío → la respuesta
  es final y retornamos el texto. Si hay tool_calls, ejecutamos las tools
  y seguimos iterando.
"""

from __future__ import annotations

import json
import logging

from core.domain.entities.message import Message, Role
from core.domain.errors import ToolLoopMaxIterationsError
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.tool_port import IToolExecutor

logger = logging.getLogger(__name__)


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
    last_text: str = ""

    for iteration in range(max_iterations):
        response = await llm.complete(
            working_messages,
            system_prompt,
            tools=tool_schemas if tool_schemas else None,
        )
        last_text = response.text

        if not response.tool_calls:
            return response.text

        # Iteración con tool calls. El assistant puede haber emitido texto
        # narrando lo que va a hacer ("ok, voy a buscar esto...") junto
        # con los tool_calls en la MISMA respuesta. Ese texto se preserva
        # en el mensaje assistant para que el propio LLM lo vea en la
        # siguiente iteración. La emisión al sink inbound (mensajes
        # intermedios al usuario) se conectará en una fase posterior.
        working_messages.append(
            Message(
                role=Role.ASSISTANT,
                content=response.text,
                tool_calls=response.tool_calls,
            )
        )

        for tc in response.tool_calls:
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
    raise ToolLoopMaxIterationsError(last_response=last_text)
