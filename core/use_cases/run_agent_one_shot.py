"""
RunAgentOneShotUseCase — ejecución stateless de un agente para una sola tarea.

Usado internamente por DelegateTool (y cualquier consumidor futuro que necesite
una ejecución aislada sin side-effects sobre el estado persistido).

Contratos clave:
- REQ-OS-1: NO carga ni persiste historial. NO lee el memory digest.
- REQ-OS-2: Usa `system_prompt` del caller verbatim cuando no es None.
             Si es None, usa el system prompt por defecto del agente.
- REQ-OS-3: `asyncio.wait_for` con timeout_seconds; ToolLoopMaxIterationsError
             propagados al caller — ninguno se captura aquí.
- REQ-OS-4: Pasa `tool_registry.get_schemas()` completo al LLM — sin RAG.
- REQ-DG-9: Filtra la tool "delegate" de los schemas antes de pasarlos al loop
             (prevención de recursión por construcción).
"""

from __future__ import annotations

import asyncio
import logging

from core.domain.entities.message import Message, Role
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.tool_port import IToolExecutor
from core.use_cases._tool_loop import run_tool_loop
from infrastructure.config import AgentConfig

logger = logging.getLogger(__name__)

_DELEGATE_TOOL_NAME = "delegate"


class RunAgentOneShotUseCase:
    """
    Ejecuta un agente de forma stateless para una única tarea.

    No carga ni escribe historial. No lee digest. No aplica RAG sobre tools.
    Excluye la tool "delegate" del schema del hijo (REQ-DG-9).
    """

    def __init__(
        self,
        llm: ILLMProvider,
        tools: IToolExecutor,
        agent_config: AgentConfig,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._cfg = agent_config

    async def execute(
        self,
        task: str,
        system_prompt: str | None,
        max_iterations: int,
        timeout_seconds: int,
    ) -> str:
        """
        Ejecuta el agente sobre `task` y retorna la respuesta final del LLM.

        Args:
            task: Texto de la tarea a ejecutar (mensaje inicial del user).
            system_prompt: Prompt de sistema a usar. Si es None, usa el
                           system_prompt por defecto del agente (sin digest
                           ni sections extra — solo el base prompt).
            max_iterations: Límite de iteraciones del loop de tools.
                            Al superarse, propaga ToolLoopMaxIterationsError.
            timeout_seconds: Límite de tiempo en segundos. Al superarse,
                             propaga asyncio.TimeoutError.

        Returns:
            Respuesta final en texto del LLM.

        Raises:
            asyncio.TimeoutError: Si la ejecución supera timeout_seconds.
            ToolLoopMaxIterationsError: Si el loop supera max_iterations.
        """
        # REQ-OS-2: usa el system_prompt del caller, o el default del agente.
        effective_prompt = system_prompt if system_prompt is not None else self._cfg.system_prompt

        # REQ-OS-4: toolkit completo sin RAG.
        # REQ-DG-9: excluir "delegate" para prevenir recursión por construcción.
        all_schemas = self._tools.get_schemas()
        # ToolRegistry.get_schemas() returns {"type": "function", "function": {"name": ...}}.
        # The name lives at s["function"]["name"], not at s["name"].
        tool_schemas = [
            s for s in all_schemas
            if s.get("function", {}).get("name") != _DELEGATE_TOOL_NAME
        ]

        if len(tool_schemas) < len(all_schemas):
            logger.debug(
                "RunAgentOneShotUseCase: tool '%s' excluida del schema del hijo (REQ-DG-9)",
                _DELEGATE_TOOL_NAME,
            )

        # REQ-OS-1: historial limpio — solo el mensaje de la tarea actual.
        messages = [Message(role=Role.USER, content=task)]

        # REQ-OS-3: timeout + iteraciones.
        # asyncio.TimeoutError y ToolLoopMaxIterationsError se propagan al caller.
        response = await asyncio.wait_for(
            run_tool_loop(
                llm=self._llm,
                tools=self._tools,
                messages=messages,
                system_prompt=effective_prompt,
                tool_schemas=tool_schemas,
                max_iterations=max_iterations,
                circuit_breaker_threshold=self._cfg.tools.circuit_breaker_threshold,
                agent_id=self._cfg.id,
            ),
            timeout=timeout_seconds,
        )

        return response
