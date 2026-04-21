"""
DelegateTool — delega una tarea a un agente hijo vía RunAgentOneShotUseCase.

Implementa ITool con name="delegate". Satisface:
- REQ-DG-2: allow-list check antes de resolver el registry.
- REQ-DG-3: registry lookup falla → unknown_agent.
- REQ-DG-4: child retorna texto → parse_delegation_result → DelegationResult.
- REQ-DG-5: parse failure → result_parse_error (manejado por el parser).
- REQ-DG-6 / REQ-DG-8: todos los fallos producen DelegationResult; NUNCA propaga.
- REQ-DG-9: recursión imposible por construcción — RunAgentOneShot ya filtra
             la tool "delegate" de los schemas del hijo.

IMPORTANTE — batch-3 decision: RunAgentOneShotUseCase es dueño del asyncio.wait_for.
DelegateTool NO envuelve en wait_for; solo captura asyncio.TimeoutError que el use
case propaga cuando se excede timeout_seconds.

Task 6.2: DelegateTool construye un effective_system_prompt que antepone el prompt base
(del caller o el default del agente hijo) y agrega _RESULT_FORMAT_FOOTER al final.
El hijo SIEMPRE recibe las instrucciones de formato — sin esto, parse_delegation_result
devolvería result_parse_error en cada llamada.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

from core.domain.errors import ToolLoopMaxIterationsError
from core.domain.value_objects.delegation_result import DelegationResult
from core.ports.outbound.tool_port import ITool, ToolResult
from core.use_cases._result_parser import parse_delegation_result

if TYPE_CHECKING:
    from infrastructure.container import AgentContainer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result-format footer — task 6.2
#
# This constant is appended to every effective system prompt sent to the child
# agent. It instructs the child to end its response with a fenced ```json```
# block containing the required fields, which parse_delegation_result then
# extracts. Without this footer the child has no contract and the parser
# always returns result_parse_error.
# ---------------------------------------------------------------------------
_RESULT_FORMAT_FOOTER = """\
You MUST end your response with a fenced JSON block in the following format, exactly:

```json
{
  "status": "success" | "failed",
  "summary": "one-sentence result",
  "details": "full result or null",
  "reason": "failure reason code or null"
}
```

This is the last thing in your response. Do not add any text after the closing fence. \
The `status` must be `"success"` if you completed the task, or `"failed"` if you could not."""


class DelegateTool(ITool):
    """
    Tool que delega una tarea a otro agente (hijo) y retorna un resultado estructurado.

    El hijo se ejecuta de forma stateless vía RunAgentOneShotUseCase. No persiste
    historial, no lee digest, y no tiene acceso a la tool "delegate" en su schema
    (recursión imposible por construcción — REQ-DG-9).
    """

    name = "delegate"
    # NOTE: keep this description as plain text — no markdown, no newlines.
    # Structured guidance on when to delegate lives in the agent-discovery
    # section injected by AgentContainer.wire_delegation.
    description = (
        "Delegate a task to a specialized agent when the task requires expertise or tools you don't have, "
        "or involves multiple steps better handled end-to-end by another agent. "
        "Returns a structured result with status, summary, details, and reason."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "ID of the target agent to delegate the task to.",
            },
            "task": {
                "type": "string",
                "description": "Description of the work to delegate to the target agent.",
            },
            "system_prompt": {
                "type": "string",
                "description": (
                    "Optional override for the child agent's system prompt. "
                    "When omitted, the child uses its own default system prompt."
                ),
            },
        },
        "required": ["agent_id", "task"],
    }

    def __init__(
        self,
        *,
        allowed_targets: list[str],
        get_agent_container: Callable[[str], "AgentContainer | None"],
        max_iterations_per_sub: int,
        timeout_seconds: int,
    ) -> None:
        """
        Args:
            allowed_targets: Lista de agent_ids permitidos como destino. Si está vacía,
                             todos los agentes registrados son válidos. Proviene de
                             agent_config.delegation.allowed_targets del agente padre.
            get_agent_container: Callable que recibe un agent_id y retorna su
                                 AgentContainer, o None si no existe en el registry.
                                 Provisto por AppContainer.get_agent (task 5.1).
            max_iterations_per_sub: Límite de iteraciones para el loop del agente hijo.
                                    Proviene de global_config.delegation.max_iterations_per_sub.
            timeout_seconds: Límite de tiempo en segundos para la ejecución del hijo.
                             Proviene de global_config.delegation.timeout_seconds.
        """
        self._allowed_targets = allowed_targets
        self._get_agent_container = get_agent_container
        self._max_iterations_per_sub = max_iterations_per_sub
        self._timeout_seconds = timeout_seconds

    async def execute(  # type: ignore[override]
        self,
        agent_id: str,
        task: str,
        system_prompt: str | None = None,
        **kwargs,
    ) -> ToolResult:
        """
        Delega `task` al agente `agent_id` y retorna un ToolResult.

        El campo `output` del ToolResult es siempre un JSON string serializado de
        DelegationResult (valid o error). NUNCA propaga excepciones — REQ-DG-8.

        Flow:
        1. Allow-list check (REQ-DG-2) → target_not_allowed
        2. Registry lookup (REQ-DG-3) → unknown_agent
        3. Construir effective_system_prompt (base + _RESULT_FORMAT_FOOTER) — task 6.2
        4. Llamar al one-shot use case del hijo
        5. Capturar TimeoutError → timeout
        6. Capturar ToolLoopMaxIterationsError → max_iterations_exceeded
        7. Capturar Exception genérica → child_exception:<Type>
        8. On success: parse_delegation_result(raw)
        9. Serializar DelegationResult → ToolResult.output JSON
        """
        # -----------------------------------------------------------------------
        # 1. Allow-list check — REQ-DG-2
        # -----------------------------------------------------------------------
        if self._allowed_targets and agent_id not in self._allowed_targets:
            logger.warning(
                "DelegateTool: agente '%s' no está en la allow-list %s",
                agent_id,
                self._allowed_targets,
            )
            result = DelegationResult(
                status="failed",
                summary=f"Agent '{agent_id}' is not in the allowed delegation targets.",
                reason="target_not_allowed",
            )
            return self._build_tool_result(result)

        # -----------------------------------------------------------------------
        # 2. Registry lookup — REQ-DG-3
        # -----------------------------------------------------------------------
        container = self._get_agent_container(agent_id)
        if container is None:
            logger.warning("DelegateTool: agente '%s' no encontrado en el registry", agent_id)
            result = DelegationResult(
                status="failed",
                summary=f"Agent '{agent_id}' is not registered in the application registry.",
                reason="unknown_agent",
            )
            return self._build_tool_result(result)

        # -----------------------------------------------------------------------
        # 3. Retrieve child's one-shot use case
        #    NOTE para task 5.1: el atributo en AgentContainer se llama
        #    `run_agent_one_shot` (naming consistente con `run_agent`).
        # -----------------------------------------------------------------------
        child_one_shot = container.run_agent_one_shot

        # -----------------------------------------------------------------------
        # 4. Build effective system prompt — task 6.2
        #
        #    _RESULT_FORMAT_FOOTER ALWAYS appended so the child knows to emit a
        #    trailing ```json``` block. Without it, parse_delegation_result returns
        #    result_parse_error on every call.
        #
        #    Strategy:
        #    - caller passed system_prompt → use it as base
        #    - caller passed None → fall back to child's default system_prompt
        #      (container.agent_config.system_prompt, already resolved above)
        #
        #    Never-raises: any AttributeError on agent_config is caught here and
        #    falls back to footer-only to preserve the DelegateTool never-raises
        #    guarantee (REQ-DG-8).
        # -----------------------------------------------------------------------
        try:
            if system_prompt is not None:
                base_prompt = system_prompt
            else:
                base_prompt = container.agent_config.system_prompt
            effective_system_prompt = base_prompt + "\n\n" + _RESULT_FORMAT_FOOTER
        except Exception as _prompt_exc:  # noqa: BLE001
            logger.warning(
                "DelegateTool: no se pudo construir el effective_system_prompt para '%s': %s — "
                "usando footer solo",
                agent_id,
                _prompt_exc,
            )
            effective_system_prompt = _RESULT_FORMAT_FOOTER

        # -----------------------------------------------------------------------
        # 5–8. Call child + catch all failure modes (REQ-DG-6 / REQ-DG-8)
        # -----------------------------------------------------------------------
        try:
            raw = await child_one_shot.execute(
                task=task,
                system_prompt=effective_system_prompt,
                max_iterations=self._max_iterations_per_sub,
                timeout_seconds=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "DelegateTool: agente '%s' excedió timeout_seconds=%d",
                agent_id,
                self._timeout_seconds,
            )
            result = DelegationResult(
                status="failed",
                summary=f"Agent '{agent_id}' exceeded the configured timeout ({self._timeout_seconds}s).",
                reason="timeout",
            )
            return self._build_tool_result(result)
        except ToolLoopMaxIterationsError:
            logger.warning(
                "DelegateTool: agente '%s' alcanzó max_iterations=%d",
                agent_id,
                self._max_iterations_per_sub,
            )
            result = DelegationResult(
                status="failed",
                summary=(
                    f"Agent '{agent_id}' exceeded the maximum number of iterations "
                    f"({self._max_iterations_per_sub})."
                ),
                reason="max_iterations_exceeded",
            )
            return self._build_tool_result(result)
        except Exception as exc:  # noqa: BLE001
            exc_type = type(exc).__name__
            logger.exception("DelegateTool: agente '%s' lanzó %s", agent_id, exc_type)
            result = DelegationResult(
                status="failed",
                summary=f"Agent '{agent_id}' raised an unhandled exception: {exc_type}.",
                details=str(exc),
                reason=f"child_exception:{exc_type}",
            )
            return self._build_tool_result(result)

        # -----------------------------------------------------------------------
        # 7. Parse result (REQ-DG-4 / REQ-DG-5)
        #    parse_delegation_result NUNCA lanza — devuelve DelegationResult con
        #    reason="result_parse_error" si el texto no contiene un bloque ```json```.
        # -----------------------------------------------------------------------
        result = parse_delegation_result(raw)
        logger.debug("DelegateTool: agente '%s' completó con status='%s'", agent_id, result.status)
        return self._build_tool_result(result)

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    def _build_tool_result(self, delegation_result: DelegationResult) -> ToolResult:
        """Serializa DelegationResult a JSON y lo envuelve en ToolResult."""
        output_json = delegation_result.model_dump_json()
        success = delegation_result.status == "success"
        return ToolResult(
            tool_name=self.name,
            output=output_json,
            success=success,
            error=delegation_result.reason if not success else None,
        )
