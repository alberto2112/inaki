"""Wiring del módulo agents: delegación (sync y background) y el sub-agente efímero.

Único fichero del módulo con permiso para importar ``inaki.config`` y el
``wiring.py`` de ``llm``: construir un hijo efímero ES resolver config
(``inherit`` contra el caller) y, a veces, instanciar un LLM distinto.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from inaki.agents.delegation.background_queue import BackgroundDelegationQueueAdapter
from inaki.agents.delegation.delegate_tool import DelegateTool, DelegationCaller
from inaki.config import (
    SUBAGENT_DEFAULTS,
    AgentConfig,
    GlobalConfig,
    assemble_agent_config,
)
from inaki.config.merge import deep_merge, resolver_inherit
from inaki.kernel.domain.value_objects.agent_settings import OneShotSettings
from inaki.kernel.ports.outbound.background_delegation_port import IBackgroundDelegationQueue
from inaki.kernel.ports.outbound.channel_port import IChannelSender
from inaki.kernel.ports.outbound.llm_dispatcher_port import ILLMDispatcher
from inaki.kernel.ports.outbound.llm_port import ILLMProvider
from inaki.kernel.ports.outbound.tool_port import IToolExecutor
from inaki.kernel.ports.outbound.turn_tracer_port import ITurnTracer
from inaki.kernel.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from inaki.llm.wiring import LLMProviderFactory

logger = logging.getLogger(__name__)


def build_background_queue(
    global_cfg: GlobalConfig,
    *,
    dispatcher: ILLMDispatcher,
    one_shot_resolver: Callable[[str, str], RunAgentOneShotUseCase | None],
    result_sender: IChannelSender,
) -> BackgroundDelegationQueueAdapter:
    return BackgroundDelegationQueueAdapter(
        dispatcher=dispatcher,
        one_shot_resolver=one_shot_resolver,
        max_iterations_per_sub=global_cfg.delegation.max_iterations_per_sub,
        timeout_seconds=global_cfg.delegation.timeout_seconds,
        max_concurrent=3,
        result_sender=result_sender,
    )


def build_ephemeral_child(
    definition_raw: dict,
    *,
    caller_cfg: AgentConfig,
    caller_llm: ILLMProvider,
    tools: IToolExecutor,
    tracer: ITurnTracer,
    thinking_indicator: bool,
) -> RunAgentOneShotUseCase:
    """Instancia efímera one-shot de un sub-agente resuelta contra el CALLER.

    El sub-agente NO es un container pre-construido: cada delegación arma una
    instancia nueva cuya config se resuelve contra el caller vía ``inherit``
    (misma definición + callers distintos → instancias distintas heredando cada
    una de su padre). Es one-shot y se descarta al terminar.

    Resolución: ``resolver_inherit(deep_merge(SUBAGENT_DEFAULTS, definition_raw), parent_raw)``
    con ``parent_raw`` = config EFECTIVA del caller. El hijo hereda el registry
    ``providers`` del caller (corre con sus credenciales; un sub con providers
    propios los pisa).

    Recursos: SIEMPRE las tools del CALLER (el sub recorta el subset visible con
    ``tools.allowed``); sin embedder (one-shot sin RAG). LLM: si la config llm
    efectiva del hijo == la del caller → se reusa la instancia; si difiere →
    instancia nueva vía factory.
    """
    parent_raw = caller_cfg.model_dump()
    merged = resolver_inherit(deep_merge(SUBAGENT_DEFAULTS, definition_raw), parent_raw)
    merged["providers"] = deep_merge(
        parent_raw.get("providers") or {}, merged.get("providers") or {}
    )
    child_cfg = assemble_agent_config(merged)
    child_llm = (
        caller_llm
        if child_cfg.llm == caller_cfg.llm
        else LLMProviderFactory.create(child_cfg.llm, child_cfg.providers)
    )
    allowed = child_cfg.tools.allowed
    return RunAgentOneShotUseCase(
        llm=child_llm,
        tools=tools,
        settings=OneShotSettings(
            agent_id=child_cfg.id,
            system_prompt=child_cfg.system_prompt,
            circuit_breaker_threshold=child_cfg.tools.circuit_breaker_threshold,
            request_delay_seconds=child_cfg.llm.request_delay_seconds,
            allowed_tools=frozenset(allowed) if allowed is not None else None,
        ),
        thinking_indicator=thinking_indicator,
        tracer=tracer,
    )


def build_delegate_tool(
    global_cfg: GlobalConfig,
    *,
    allowed_targets: list[str],
    build_child: Callable[[str], RunAgentOneShotUseCase | None],
    caller_agent_id: str,
    caller: DelegationCaller,
    queue: IBackgroundDelegationQueue | None,
) -> DelegateTool:
    return DelegateTool(
        allowed_targets=allowed_targets,
        build_child=build_child,
        max_iterations_per_sub=global_cfg.delegation.max_iterations_per_sub,
        timeout_seconds=global_cfg.delegation.timeout_seconds,
        caller_agent_id=caller_agent_id,
        caller_container=caller,
        queue=queue,
    )


class _DescripcionDeAgente(Protocol):
    """Lo que la sección de descubrimiento necesita de un agente ya construido
    (fallback cuando el registry no tiene el delta crudo del target)."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def tool_names(self) -> list[str]: ...


def build_discovery_section(
    agent_id: str,
    sub_agent_ids: list[str] | None,
    *,
    get_sub_agent_raw: Callable[[str], dict | None] | None,
    describir_agente: Callable[[str], _DescripcionDeAgente | None],
) -> str:
    """Sección del system prompt que lista los targets de delegación.

    Fuente PRIMARIA: el delta crudo del registry (``get_sub_agent_raw``) — el
    hijo efímero opera con las tools del CALLER acotadas por su
    ``tools.allowed``, así que la lista de tools de un container pre-built del
    sub sería mentira. Fallback: el agente construido (tests parciales sin
    registry). Un target sin raw NI agente se saltea. Vacía si no hay targets.
    """
    target_ids = sub_agent_ids or []
    if not target_ids:
        return ""
    lines: list[str] = []
    for target_id in target_ids:
        raw = get_sub_agent_raw(target_id) if get_sub_agent_raw is not None else None
        if raw is not None:
            name = raw.get("name") or target_id
            description = " ".join(str(raw.get("description") or "").split())
            allowed = (raw.get("tools") or {}).get("allowed") or None
            tool_list = (
                ", ".join(allowed) + " (subset of this agent's toolkit)"
                if allowed
                else "inherits this agent's full toolkit"
            )
        else:
            target = describir_agente(target_id)
            if target is None:
                logger.debug(
                    "Agente '%s': target '%s' not found in registry — skipping in discovery",
                    agent_id,
                    target_id,
                )
                continue
            name = target.name
            description = target.description
            tool_list = ", ".join(target.tool_names) if target.tool_names else "(no tools)"
        lines.append(f"- **{target_id}** ({name}) — {description}.")
        lines.append(f"  Tools: {tool_list}")
    if not lines:
        return ""
    header = (
        "# Available agents for delegation\n\n"
        "You can delegate tasks to other agents via the `delegate` tool.\n\n"
        "## When to delegate\n\n"
        "- The task matches another agent's specialty "
        "(see their description and tools below).\n"
        "- You lack a tool that the target agent has.\n"
        "- The task requires multiple tool calls to complete, especially multi-step "
        'workflows like: "search the web about X, summarize the highlights, and send '
        'the result to Y". Delegating keeps your context clean and lets a specialized '
        "agent orchestrate the steps.\n\n"
        "## When NOT to delegate\n\n"
        "- The task is trivial or you already have the tools to solve it in 1-2 steps.\n"
        "- You need tight back-and-forth with the user — the child is stateless and "
        "returns a single structured result.\n"
        "- You already delegated the same task and it failed — try a different approach "
        "or ask the user.\n\n"
        "## Available agents\n"
    )
    return "\n" + header + "\n" + "\n".join(lines)
