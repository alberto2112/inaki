"""
Integration tests for agent-delegation: tasks 7.1, 7.2, 7.3.

These tests wire real AgentContainer instances (constructed without file IO via
__new__ + attribute injection) and run full end-to-end delegation scenarios with
mocked LLM ports and real tool registries.

Coverage:
- Task 7.1 — End-to-end happy path (REQ-DG-4)
- Task 7.2 — All failure modes (REQ-DG-2, REQ-DG-3, REQ-DG-5, REQ-DG-6, REQ-DG-8)
- Task 7.3 — Sub-agent schema excludes delegate tool (REQ-DG-9)
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.outbound.tools.delegate_tool import DelegateTool, _RESULT_FORMAT_FOOTER
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.domain.value_objects.conversation_state import ConversationState
from core.domain.value_objects.delegation_result import DelegationResult
from core.domain.value_objects.llm_response import LLMResponse
from core.use_cases.run_agent import RunAgentUseCase
from core.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from infrastructure.config import (
    AgentConfig,
    AgentDelegationConfig,
    ChatHistoryConfig,
    DelegationConfig,
    EmbeddingConfig,
    GlobalConfig,
    LLMConfig,
    MemoriesConfig,
    ProviderConfig,
)
from core.domain.value_objects.agent_settings import OneShotSettings
from infrastructure.container import AgentContainer, build_run_agent_settings


# ===========================================================================
# Shared helpers and fixtures
# ===========================================================================


class FakeEmbedder:
    """Minimal embedder — no real model needed."""

    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _make_agent_config(
    agent_id: str,
    delegation_enabled: bool,
    allowed_targets: list[str] | None = None,
    description: str | None = None,
) -> AgentConfig:
    """
    Build a minimal valid AgentConfig. All non-required fields default.
    delegation.enabled=False → no delegate tool wired.
    delegation.enabled=True  → wire_delegation will register delegate tool.
    """
    return AgentConfig(
        id=agent_id,
        name=agent_id.capitalize(),
        description=description or f"Integration test agent {agent_id}",
        system_prompt=f"You are {agent_id}.",
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memories=MemoriesConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/integ_history.db"),
        delegation=AgentDelegationConfig(
            enabled=delegation_enabled,
            allowed_targets=allowed_targets or [],
        ),
        providers={"openrouter": ProviderConfig(api_key="test-key")},
    )


def _make_global_config(
    max_iterations_per_sub: int = 10,
    timeout_seconds: int = 60,
) -> GlobalConfig:
    from infrastructure.config import (
        AppConfig,
        SchedulerConfig,
        SkillsConfig,
        ToolsConfig,
        WorkspaceConfig,
    )

    return GlobalConfig(
        app=AppConfig(ext_dirs=[]),
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memories=MemoriesConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/integ_history.db"),
        skills=SkillsConfig(),
        tools=ToolsConfig(),
        scheduler=SchedulerConfig(),
        workspace=WorkspaceConfig(),
        delegation=DelegationConfig(
            max_iterations_per_sub=max_iterations_per_sub,
            timeout_seconds=timeout_seconds,
        ),
        providers={"openrouter": ProviderConfig(api_key="test-key")},
    )


def _make_scripted_llm(responses: list[LLMResponse | str]) -> AsyncMock:
    """
    Return a mock LLM whose complete() method yields scripted responses in order.
    Raises AssertionError if called more times than responses are available.

    Cada entrada puede ser un ``LLMResponse`` directamente o un ``str`` (se
    envuelve en ``LLMResponse.of_text`` por conveniencia).
    """
    llm = AsyncMock()
    call_count = [0]

    normalized: list[LLMResponse] = [
        r if isinstance(r, LLMResponse) else LLMResponse.of_text(r) for r in responses
    ]

    async def _complete(messages, system_prompt, tools=None):
        idx = call_count[0]
        call_count[0] += 1
        if idx >= len(normalized):
            raise AssertionError(
                f"LLM mock called {call_count[0]} times but only "
                f"{len(normalized)} response(s) scripted."
            )
        return normalized[idx]

    llm.complete.side_effect = _complete
    return llm


def _make_dummy_tool(name: str) -> MagicMock:
    """Create a minimal tool mock that satisfies ITool interface."""
    tool = MagicMock()
    tool.name = name
    tool.description = f"Dummy tool {name}"
    tool.parameters_schema = {"type": "object", "properties": {}}
    return tool


def _build_container(
    agent_config: AgentConfig,
    global_config: GlobalConfig,
    llm: AsyncMock,
    extra_tools: list[MagicMock] | None = None,
) -> AgentContainer:
    """
    Build an AgentContainer without any real IO (no filesystem, no factories).
    Uses __new__ + attribute injection, matching the pattern in test_container.py.
    """
    container = AgentContainer.__new__(AgentContainer)
    container.agent_config = agent_config
    container._global_config = global_config
    container._delegation_wired = False
    container._llm = llm
    container._embedder = FakeEmbedder()
    container._tools = ToolRegistry(embedder=container._embedder)

    # Register at least one dummy tool so get_schemas() is non-empty
    container._tools.register(_make_dummy_tool("dummy_tool"))
    if extra_tools:
        for t in extra_tools:
            container._tools.register(t)

    # run_agent is needed by wire_delegation (set_extra_system_sections)
    container.run_agent = RunAgentUseCase(
        llm=llm,
        memory=AsyncMock(search=AsyncMock(return_value=[])),
        embedder=container._embedder,
        skills=AsyncMock(list_all=AsyncMock(return_value=[]), retrieve=AsyncMock(return_value=[])),
        history=AsyncMock(
            load=AsyncMock(return_value=[]),
            append=AsyncMock(),
            load_state=AsyncMock(return_value=ConversationState()),
            save_state=AsyncMock(),
        ),
        tools=container._tools,
        settings=build_run_agent_settings(agent_config),
    )

    # Every container gets run_agent_one_shot unconditionally (mirrors __init__ behaviour).
    container.run_agent_one_shot = RunAgentOneShotUseCase(
        llm=llm,
        tools=container._tools,
        settings=OneShotSettings(
            agent_id=agent_config.id,
            system_prompt=agent_config.system_prompt,
            circuit_breaker_threshold=agent_config.tools.circuit_breaker_threshold,
        ),
    )

    return container


def _minimal_sub_delta(child: AgentContainer) -> dict:
    """Delta crudo mínimo de un sub-agente (lo que daría ``registry.get_sub_agent_raw``).

    Solo identidad + prompt → SIN bloque ``llm`` → el hijo efímero HEREDA el llm del
    caller (default C). Para testear override, pasá un ``sub_delta`` con ``llm``.
    """
    cfg = child.agent_config
    return {
        "id": cfg.id,
        "name": cfg.name,
        "description": cfg.description,
        "system_prompt": cfg.system_prompt,
    }


def _wire_both(
    parent: AgentContainer,
    child: AgentContainer,
    *,
    sub_delta: dict | None = None,
) -> Callable[[str], AgentContainer | None]:
    """Harness de dos containers para el flujo delegate bajo C.

    El padre recibe al hijo como sub-agente disponible (discovery + allow-list) y un
    ``get_sub_agent_raw`` que devuelve el delta del hijo. La delegación construye una
    instancia EFÍMERA contra el caller (``build_ephemeral_child``) — el container del
    hijo solo se usa para la discovery section; su ``_llm`` NO se usa (el efímero hereda
    el del padre). Pasá ``sub_delta`` para forzar override (ej. ``{"llm": {...}}``).

    Returns the get_agent_container closure for use in assertions.
    """
    containers = {
        parent.agent_config.id: parent,
        child.agent_config.id: child,
    }

    def _get_agent_container(agent_id: str) -> AgentContainer | None:
        return containers.get(agent_id)

    delta = sub_delta if sub_delta is not None else _minimal_sub_delta(child)

    def _get_sub_agent_raw(agent_id: str) -> dict | None:
        return delta if agent_id == child.agent_config.id else None

    parent.wire_delegation(
        _get_agent_container,
        sub_agent_ids=[child.agent_config.id],
        get_sub_agent_raw=_get_sub_agent_raw,
    )
    child.wire_delegation(_get_agent_container)  # no-op: child no delega

    return _get_agent_container


def _tool_call_response(agent_id: str, task: str) -> LLMResponse:
    """Build a scripted LLM response that represents a delegate tool call.

    NOTA Phase 4 (REQ-DG-10): pasamos ``wait=True`` para preservar la intención
    original de estos tests (sync path con DelegationResult parseado). El nuevo
    default es async y ese path está cubierto en `test_delegate_tool.py`.
    """
    return LLMResponse(
        text_blocks=[],
        tool_calls=[
            {
                "function": {
                    "name": "delegate",
                    "arguments": json.dumps({"agent_id": agent_id, "task": task, "wait": True}),
                }
            }
        ],
        raw="",
    )


def _valid_child_response(
    status: str = "success",
    summary: str = "Task done",
    details: str | None = None,
    reason: str | None = None,
) -> str:
    """Build a valid child LLM response with a trailing ```json``` block."""
    data: dict = {"status": status, "summary": summary}
    if details is not None:
        data["details"] = details
    if reason is not None:
        data["reason"] = reason
    block = f"```json\n{json.dumps(data)}\n```"
    return f"I completed the task.\n\n{block}"


def _scripted_parent_llm(
    *,
    target: str = "child",
    task: str = "do something",
    child: object = None,
    final: str | LLMResponse = "Final answer.",
) -> AsyncMock:
    """LLM del parent que TAMBIÉN sirve los turnos del hijo (que hereda este mismo llm
    bajo C). Discrimina por schemas: si ``delegate`` está en ``tools`` → turno del PARENT
    (1ro = ``delegate(target, task)``, 2do = ``final``); si no → turno del HIJO, donde
    ``child`` define el comportamiento:

    - ``str`` / ``LLMResponse`` → respuesta del hijo (sin tool calls).
    - ``BaseException`` → el turno del hijo lanza (child_exception).
    - ``("sleep", seg)`` → duerme (para timeout).
    - ``("loop", LLMResponse)`` → devuelve siempre ese tool_call (para max_iterations).
    - ``None`` → el hijo nunca se alcanza (target_not_allowed / unknown_agent).
    """
    llm = AsyncMock()
    state = {"delegated": False}
    delegate_resp = _tool_call_response(target, task)
    final_resp = final if isinstance(final, LLMResponse) else LLMResponse.of_text(final)
    child_text: LLMResponse | None = None
    if isinstance(child, LLMResponse):
        child_text = child
    elif isinstance(child, str):
        child_text = LLMResponse.of_text(child)

    async def _complete(messages, system_prompt, tools=None):
        names = {t.get("function", {}).get("name") for t in (tools or [])}
        if "delegate" in names:  # turno del PARENT
            if not state["delegated"]:
                state["delegated"] = True
                return delegate_resp
            return final_resp
        # turno del HIJO (la tool delegate la filtra el OneShot — REQ-DG-9)
        if isinstance(child, BaseException):
            raise child
        if isinstance(child, tuple) and child and child[0] == "sleep":
            await asyncio.sleep(child[1])
            return LLMResponse.of_text("never reached")
        if isinstance(child, tuple) and child and child[0] == "loop":
            return child[1]
        return child_text

    llm.complete.side_effect = _complete
    return llm


def _child_turn_calls(parent_llm: AsyncMock) -> list:
    """Llamadas a ``parent_llm`` que fueron turnos del HIJO (sin ``delegate`` en schemas).

    Bajo C el hijo efímero hereda el llm del parent, así que sus turnos aparecen en el
    ``call_args_list`` del ``parent_llm`` — distinguibles porque el OneShot filtra
    ``delegate`` de los schemas (REQ-DG-9).
    """
    out = []
    for call in parent_llm.complete.call_args_list:
        tools = call.kwargs.get("tools") or []
        names = {t.get("function", {}).get("name") for t in tools}
        if "delegate" not in names:
            out.append(call)
    return out


# ===========================================================================
# Task 7.1 — End-to-end happy path (REQ-DG-4)
# ===========================================================================


async def test_happy_path_end_to_end(tmp_path):
    """
    Task 7.1 / REQ-DG-4: Full round-trip delegation.

    Parent LLM:
      1st call → delegate tool call targeting "child"
      2nd call → final text synthesizing child result (no more tool calls)

    Child LLM:
      1st call → valid trailing ```json``` block with status=success

    Assertions:
      1. Parent execute() returns final synthesized text.
      2. Parent LLM was called twice.
      3. Child LLM was called once.
      4. Child LLM system prompt contains _RESULT_FORMAT_FOOTER (6.2 wired).
      5. Child LLM tool_schemas does NOT include "delegate" (REQ-DG-9).
      6. Parent system prompt contains child's id (6.1 wired).
      7. DelegationResult embedded in the chain has status="success".
    """
    global_cfg = _make_global_config(max_iterations_per_sub=10, timeout_seconds=60)

    # Bajo C el hijo efímero hereda el llm del parent → el MISMO parent_llm sirve el turno
    # del hijo. _scripted_parent_llm intercala: delegate → child_response → final.
    parent_llm = _scripted_parent_llm(
        target="child",
        task="compute the sum of 2 and 2",
        child=_valid_child_response(
            status="success",
            summary="2 + 2 = 4",
            details="Computed by basic arithmetic.",
        ),
        final="The specialist computed 2 + 2 = 4.",
    )

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["child"],
        description="Coordinator agent",
    )
    child_cfg = _make_agent_config(
        agent_id="child",
        delegation_enabled=True,
        allowed_targets=[],
        description="Arithmetic specialist",
    )

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    # El llm del container del hijo NO se usa bajo C (el efímero hereda el del parent):
    # un sentinela que lanza si se invoca prueba ese invariante.
    child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))

    _wire_both(parent_container, child_container)

    # --- Execute the full chain ---
    result = await parent_container.run_agent.execute("What is 2 + 2?")

    # Assertion 1: final text is the parent's final response
    assert result == "The specialist computed 2 + 2 = 4."

    # Assertion 2: parent_llm called 3 veces (delegate + turno del hijo heredado + final)
    assert parent_llm.complete.call_count == 3, (
        f"parent_llm must be called 3 times (delegate, child turn, final). "
        f"Called: {parent_llm.complete.call_count}"
    )

    # Assertion 3: hubo EXACTAMENTE un turno del hijo (sin 'delegate' en schemas)
    child_calls = _child_turn_calls(parent_llm)
    assert len(child_calls) == 1, f"Debe haber 1 turno del hijo. Hubo: {len(child_calls)}"

    # Assertion 4: el turno del hijo lleva _RESULT_FORMAT_FOOTER (6.2 wired)
    child_system_prompt = child_calls[0].args[1]
    assert _RESULT_FORMAT_FOOTER in child_system_prompt, (
        "El system prompt del hijo debe contener _RESULT_FORMAT_FOOTER (task 6.2)"
    )

    # Assertion 5: los schemas del turno del hijo NO incluyen 'delegate' (REQ-DG-9)
    child_schema_names = [
        s.get("function", {}).get("name") for s in (child_calls[0].kwargs.get("tools") or [])
    ]
    assert "delegate" not in child_schema_names, (
        f"Los schemas del hijo NO deben incluir 'delegate' (REQ-DG-9). Got: {child_schema_names}"
    )

    # Assertion 6: el system prompt del parent contiene el id del hijo (discovery, 6.1)
    parent_first_system_prompt = parent_llm.complete.call_args_list[0].args[1]
    assert "child" in parent_first_system_prompt, (
        "El system prompt del parent debe nombrar al sub-agente 'child' (discovery, task 6.1)"
    )

    # Assertion 7: el DelegationResult inyectado al parent (última llamada) es success
    dr = DelegationResult.model_validate_json(
        parent_llm.complete.call_args_list[-1].args[0][-1].content
    )
    assert dr.status == "success", f"DelegationResult must be status=success. Got: {dr.status}"


# ===========================================================================
# Task 7.2 — All failure modes (REQ-DG-2, REQ-DG-3, REQ-DG-5, REQ-DG-6, REQ-DG-8)
# ===========================================================================


async def _run_delegation_and_extract_result(
    parent_container: AgentContainer,
    task: str = "do something",
) -> DelegationResult:
    """
    Run the parent's execute() and extract the DelegationResult from the LAST parent
    LLM call's messages (que es el turno final del parent: lleva el tool result como
    último mensaje). Usamos la ÚLTIMA llamada — no un índice fijo — porque bajo C el
    hijo efímero hereda el llm del parent e intercala turnos: el conteo de llamadas
    depende de si el hijo se alcanzó (target_not_allowed/unknown_agent no lo alcanzan).
    """
    await parent_container.run_agent.execute(task)
    parent_llm = parent_container._llm
    last_call_messages = parent_llm.complete.call_args_list[-1].args[0]  # type: ignore[attr-defined]
    tool_result_msg = last_call_messages[-1]
    return DelegationResult.model_validate_json(tool_result_msg.content)


async def test_failure_target_not_allowed():
    """
    REQ-DG-2: Parent delegates to "evil_agent" which is not in allowed_targets.
    DelegationResult.reason must be exactly "target_not_allowed".
    The closure is NEVER called (no registry lookup for disallowed target).
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["child"],  # "evil_agent" is NOT allowed
    )
    child_cfg = _make_agent_config(agent_id="child", delegation_enabled=True, allowed_targets=[])

    parent_llm = _make_scripted_llm(
        [
            _tool_call_response("evil_agent", "do something sneaky"),
            "I could not complete the task.",  # final answer
        ]
    )
    child_llm = _make_scripted_llm([])  # child must NEVER be called

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, child_llm)
    _wire_both(parent_container, child_container)

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason == "target_not_allowed", f"Expected 'target_not_allowed', got {dr.reason!r}"
    # Child LLM must never be called
    assert child_llm.complete.call_count == 0, (
        "Child LLM must NOT be invoked when target is not allowed"
    )


async def test_failure_unknown_agent():
    """
    REQ-DG-3: Parent delegates to "ghost" which is in allowed_targets but NOT
    in the container dict → closure returns None → reason "unknown_agent".
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["ghost"],  # allowed, but won't be in containers dict
    )

    parent_llm = _make_scripted_llm(
        [
            _tool_call_response("ghost", "haunt the server"),
            "I could not find the agent.",
        ]
    )

    # Only build the parent container. "ghost" is NOT in the container registry.
    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)

    # Wire manually: closure only has "parent", not "ghost". Ghost es el sub-agente.
    containers = {"parent": parent_container}

    def _get_container(agent_id: str) -> AgentContainer | None:
        return containers.get(agent_id)

    parent_container.wire_delegation(_get_container, sub_agent_ids=["ghost"])

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason == "unknown_agent", f"Expected 'unknown_agent', got {dr.reason!r}"


async def test_failure_result_parse_error_no_json_block():
    """
    REQ-DG-5 (no block): Child LLM returns plain text with no ```json``` block.
    DelegationResult.reason must be exactly "result_parse_error".
    """
    global_cfg = _make_global_config()
    parent_cfg = _make_agent_config(
        agent_id="parent", delegation_enabled=True, allowed_targets=["child"]
    )
    child_cfg = _make_agent_config(agent_id="child", delegation_enabled=True, allowed_targets=[])

    parent_llm = _scripted_parent_llm(
        target="child",
        task="do something",
        child="I did the thing but forgot to format the result.",  # sin bloque json
        final="Child could not complete the task.",
    )

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))
    _wire_both(parent_container, child_container)

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason == "result_parse_error", f"Expected 'result_parse_error', got {dr.reason!r}"


async def test_failure_result_parse_error_invalid_json_in_block():
    """
    REQ-DG-5 (invalid JSON): Child returns text with a ```json``` block
    containing syntactically invalid JSON.
    DelegationResult.reason must be exactly "result_parse_error".
    """
    global_cfg = _make_global_config()
    parent_cfg = _make_agent_config(
        agent_id="parent", delegation_enabled=True, allowed_targets=["child"]
    )
    child_cfg = _make_agent_config(agent_id="child", delegation_enabled=True, allowed_targets=[])

    parent_llm = _scripted_parent_llm(
        target="child",
        task="do something",
        child="Some output\n```json\n{not: valid json!!}\n```",
        final="Child had a parse error.",
    )

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))
    _wire_both(parent_container, child_container)

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason == "result_parse_error", f"Expected 'result_parse_error', got {dr.reason!r}"


async def test_failure_timeout():
    """
    REQ-DG-6 (timeout): Child LLM sleeps longer than timeout_seconds.
    DelegationResult.reason must be exactly "timeout".

    Uses a very short timeout (0.05s) and a child LLM that sleeps longer.
    """
    parent_cfg = _make_agent_config(
        agent_id="parent", delegation_enabled=True, allowed_targets=["child"]
    )
    child_cfg = _make_agent_config(agent_id="child", delegation_enabled=True, allowed_targets=[])

    parent_llm = _scripted_parent_llm(
        target="child",
        task="slow task",
        child=("sleep", 10),  # el turno del hijo duerme >> timeout del delegate
        final="Task timed out.",
    )

    short_timeout_global = _make_global_config(
        max_iterations_per_sub=10,
        timeout_seconds=1,
    )

    parent_container = _build_container(parent_cfg, short_timeout_global, parent_llm)
    child_container = _build_container(child_cfg, short_timeout_global, _make_scripted_llm([]))
    _wire_both(parent_container, child_container)

    # timeout chico para distinguir del sleep de 10s
    delegate_tool: DelegateTool = parent_container._tools._tools["delegate"]
    delegate_tool._timeout_seconds = 1

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason == "timeout", f"Expected 'timeout', got {dr.reason!r}"


async def test_failure_max_iterations_exceeded():
    """
    REQ-DG-6 (max_iterations): Child LLM always returns a tool call, never a
    final text. With max_iterations_per_sub=2, the loop hits the limit.

    Child has a real dummy tool registered so tool dispatch actually runs.
    DelegationResult.reason must be exactly "max_iterations_exceeded".
    """
    global_cfg = _make_global_config(max_iterations_per_sub=2, timeout_seconds=60)

    parent_cfg = _make_agent_config(
        agent_id="parent", delegation_enabled=True, allowed_targets=["child"]
    )
    child_cfg = _make_agent_config(agent_id="child", delegation_enabled=True, allowed_targets=[])

    # El hijo SIEMPRE devuelve un tool_call de "dummy_tool" → nunca texto final.
    child_tool_call = LLMResponse(
        text_blocks=[],
        tool_calls=[{"function": {"name": "dummy_tool", "arguments": "{}"}}],
        raw="",
    )
    parent_llm = _scripted_parent_llm(
        target="child",
        task="infinite loop task",
        child=("loop", child_tool_call),
        final="Child exceeded max iterations.",
    )

    # dummy_tool ejecutable EN EL PARENT: bajo C el hijo efímero usa el toolkit del caller.
    from core.ports.outbound.tool_port import ToolResult

    dummy_tool = _make_dummy_tool("dummy_tool")
    dummy_tool.execute = AsyncMock(
        return_value=ToolResult(tool_name="dummy_tool", output="ok", success=True)
    )

    parent_container = _build_container(
        parent_cfg, global_cfg, parent_llm, extra_tools=[dummy_tool]
    )
    child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))
    _wire_both(parent_container, child_container)

    # Force max_iterations_per_sub=2 on the delegate tool
    delegate_tool: DelegateTool = parent_container._tools._tools["delegate"]
    delegate_tool._max_iterations_per_sub = 2

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason == "max_iterations_exceeded", (
        f"Expected 'max_iterations_exceeded', got {dr.reason!r}"
    )


async def test_failure_child_exception():
    """
    REQ-DG-8: Child LLM raises RuntimeError("boom") on its first call.
    DelegationResult.reason must start with "child_exception:" and contain "RuntimeError".
    Parent execute() must NOT raise.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent", delegation_enabled=True, allowed_targets=["child"]
    )
    child_cfg = _make_agent_config(agent_id="child", delegation_enabled=True, allowed_targets=[])

    parent_llm = _scripted_parent_llm(
        target="child",
        task="risky task",
        child=RuntimeError("boom"),  # el turno del hijo lanza
        final="Child failed unexpectedly.",
    )

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))
    _wire_both(parent_container, child_container)

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason is not None
    assert dr.reason.startswith("child_exception:"), (
        f"Reason must start with 'child_exception:'. Got: {dr.reason!r}"
    )
    assert "RuntimeError" in dr.reason, f"Reason must contain 'RuntimeError'. Got: {dr.reason!r}"


@pytest.mark.parametrize(
    "scenario,expected_reason",
    [
        ("target_not_allowed", "target_not_allowed"),
        ("unknown_agent", "unknown_agent"),
        ("result_parse_error_no_block", "result_parse_error"),
        ("result_parse_error_invalid_json", "result_parse_error"),
        ("child_exception", "child_exception:RuntimeError"),
    ],
)
async def test_failure_modes_canonical_reason_strings(scenario: str, expected_reason: str):
    """
    REQ-DG-2, REQ-DG-3, REQ-DG-5, REQ-DG-8 — Parametrized sanity check.
    All failure reasons match the canonical strings from the design table.
    (Timeout and max_iterations_exceeded are covered by dedicated tests above
    since they require specific timing or loop configuration.)
    """
    global_cfg = _make_global_config()
    parent_cfg = _make_agent_config(
        agent_id="parent", delegation_enabled=True, allowed_targets=["child"]
    )
    child_cfg = _make_agent_config(agent_id="child", delegation_enabled=True, allowed_targets=[])

    if scenario == "target_not_allowed":
        # Delegate to an agent not in allowed_targets → el hijo NUNCA se alcanza.
        parent_llm = _scripted_parent_llm(target="other_agent", task="task", child=None, final="Done.")
        parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
        child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))
        _wire_both(parent_container, child_container)

    elif scenario == "unknown_agent":
        # "child" en allowed_targets pero SIN get_sub_agent_raw → build_child → None.
        parent_llm = _scripted_parent_llm(target="child", task="task", child=None, final="Done.")
        parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
        containers_dict = {"parent": parent_container}
        parent_container.wire_delegation(containers_dict.get, sub_agent_ids=["child"])

    elif scenario in ("result_parse_error_no_block", "result_parse_error_invalid_json"):
        child_response = (
            "Plain text, no JSON block."
            if "no_block" in scenario
            else "Some output\n```json\n{not valid}\n```"
        )
        parent_llm = _scripted_parent_llm(
            target="child", task="task", child=child_response, final="Done."
        )
        parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
        child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))
        _wire_both(parent_container, child_container)

    elif scenario == "child_exception":
        parent_llm = _scripted_parent_llm(
            target="child", task="risky task", child=RuntimeError("boom"), final="Done."
        )
        parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
        child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))
        _wire_both(parent_container, child_container)

    else:
        pytest.fail(f"Unknown scenario: {scenario}")

    # Parent must NOT raise
    result_text = await parent_container.run_agent.execute("task")
    assert isinstance(result_text, str)

    # Extraer el DelegationResult de la ÚLTIMA llamada del parent (lleva el tool result).
    parent_llm_used = parent_container._llm
    last_call_messages = parent_llm_used.complete.call_args_list[-1].args[0]  # type: ignore[attr-defined]
    tool_result_msg = last_call_messages[-1]
    dr = DelegationResult.model_validate_json(tool_result_msg.content)

    assert dr.status == "failed"
    if expected_reason.startswith("child_exception:"):
        assert dr.reason is not None and dr.reason.startswith("child_exception:"), (
            f"Expected reason starting with 'child_exception:', got: {dr.reason!r}"
        )
        assert "RuntimeError" in (dr.reason or ""), (
            f"Expected 'RuntimeError' in reason, got: {dr.reason!r}"
        )
    else:
        assert dr.reason == expected_reason, (
            f"Canonical reason mismatch. Expected: {expected_reason!r}, got: {dr.reason!r}"
        )


# ===========================================================================
# Task 7.3 — Sub-agent schema excludes delegate tool (REQ-DG-9 dedicated)
# ===========================================================================


async def test_req_dg9_child_schemas_exclude_delegate_even_when_child_has_delegation_enabled():
    """
    Task 7.3 / REQ-DG-9 dedicated test.

    Con el nuevo modelo, los sub-agentes NO reciben el tool 'delegate' en el wiring
    (wire_delegation es no-op para el child porque no se pasan sub_agent_ids).
    REQ-DG-9 se satisface en dos capas: (1) no hay tool en el registry del hijo,
    (2) RunAgentOneShotUseCase filtra 'delegate' aunque llegara por otra vía.

    This test asserts:
    5. Child registry does NOT have 'delegate' (nueva garantía por wiring).
    5b. El LLM del hijo no recibe 'delegate' en los schemas (doble garantía).
    6. El parent SÍ tiene 'delegate' en sus schemas (solo en el conversational path).
    """
    global_cfg = _make_global_config(max_iterations_per_sub=10, timeout_seconds=60)

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["child"],
        description="Coordinator",
    )
    child_cfg = _make_agent_config(
        agent_id="child",
        delegation_enabled=True,
        allowed_targets=[],
        description="Sub-agent with delegation enabled",
    )

    # Parent delega → turno del hijo (heredado) → final. Un solo parent_llm bajo C.
    parent_llm = _scripted_parent_llm(
        target="child",
        task="compute something",
        child=_valid_child_response(status="success", summary="Computed successfully."),
        final="The child computed the result.",
    )

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))
    _wire_both(parent_container, child_container)

    # Assertion 5: el container del hijo NO tiene 'delegate' (wire_delegation no-op para subs).
    assert "delegate" not in child_container._tools._tools, (
        "REQ-DG-9: child must NOT have 'delegate' tool in its registry "
        "(sub-agents never get the delegate tool wired)"
    )
    # Precondición: el parent sí tiene 'delegate'
    assert "delegate" in parent_container._tools._tools, (
        "Precondition: parent must have 'delegate' tool in its registry"
    )

    result = await parent_container.run_agent.execute("Do something complex")

    # Assertion 5b: el turno del hijo (heredado) corrió SIN 'delegate' en los schemas.
    child_calls = _child_turn_calls(parent_llm)
    assert len(child_calls) == 1, f"Debe haber 1 turno del hijo. Hubo: {len(child_calls)}"
    child_schema_names = [
        s.get("function", {}).get("name") for s in (child_calls[0].kwargs.get("tools") or [])
    ]
    assert "delegate" not in child_schema_names, (
        f"REQ-DG-9: los schemas del hijo NO deben incluir 'delegate'. Got: {child_schema_names}"
    )
    assert len(child_schema_names) >= 1, (
        "El hijo debe tener al menos un schema no-delegate (dummy_tool del caller)"
    )

    # Assertion 6: la 1ra llamada del parent SÍ recibió 'delegate' en schemas.
    parent_schema_names = [
        s.get("function", {}).get("name")
        for s in (parent_llm.complete.call_args_list[0].kwargs.get("tools") or [])
    ]
    assert "delegate" in parent_schema_names, (
        f"Los schemas del parent DEBEN incluir 'delegate' (es agente conversacional). "
        f"Got: {parent_schema_names}"
    )

    # Sanity: la cadena de delegación tuvo éxito
    assert result == "The child computed the result."


async def test_req_dg9_overlap_from_71_reconfirmed_in_73():
    """
    Task 7.3 corollary: confirm REQ-DG-9 is satisfied even in the basic
    delegation case (child with delegation.enabled=False but one-shot still
    filters delegate from schemas).

    This mirrors the 7.1 assertion #5 but as a standalone 7.3-context test.
    """
    global_cfg = _make_global_config()
    parent_cfg = _make_agent_config(
        agent_id="parent", delegation_enabled=True, allowed_targets=["child"]
    )
    child_cfg = _make_agent_config(agent_id="child", delegation_enabled=True, allowed_targets=[])

    parent_llm = _scripted_parent_llm(
        target="child",
        task="a simple task",
        child=_valid_child_response(status="success", summary="Simple task done."),
        final="Done.",
    )

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, _make_scripted_llm([]))
    _wire_both(parent_container, child_container)

    await parent_container.run_agent.execute("Do a simple task")

    # El turno del hijo (heredado) corrió SIN 'delegate' en los schemas.
    child_calls = _child_turn_calls(parent_llm)
    assert len(child_calls) == 1, f"Debe haber 1 turno del hijo. Hubo: {len(child_calls)}"
    child_schema_names = [
        s.get("function", {}).get("name") for s in (child_calls[0].kwargs.get("tools") or [])
    ]
    assert "delegate" not in child_schema_names, (
        f"REQ-DG-9: los schemas del hijo NO deben incluir 'delegate'. Got: {child_schema_names}"
    )


# ===========================================================================
# Batch 8 — Latent bug fix: child with delegation.enabled=False can be target
# ===========================================================================


async def test_child_with_delegation_disabled_can_be_delegation_target(tmp_path):
    """
    Batch 8 regression test: a child with delegation.enabled=False MUST be a valid
    delegation target after the run_agent_one_shot decoupling fix.

    Before the fix: DelegateTool.execute read container.run_agent_one_shot, which was
    only set in wire_delegation when delegation.enabled=True. A disabled child would
    AttributeError at runtime.

    After the fix: run_agent_one_shot is constructed in AgentContainer.__init__
    unconditionally, so a disabled child can be a delegation target.

    Setup:
    - parent: delegation.enabled=True, allowed_targets=["worker"]
    - worker: delegation.enabled=False (NOT just allowed_targets=[] — actually disabled)

    Assertions:
    1. parent.execute() returns without raising (no AttributeError)
    2. DelegationResult from the delegate tool call has status="success"
    3. worker container has run_agent_one_shot set (sanity check)
    4. worker tool registry does NOT contain a 'delegate' tool (REQ-DG-1 preserved)
    """
    global_cfg = _make_global_config(max_iterations_per_sub=10, timeout_seconds=60)

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["worker"],
        description="Coordinator",
    )
    # delegation.enabled=False: wire_delegation is a no-op → no 'delegate' tool registered
    worker_cfg = _make_agent_config(
        agent_id="worker",
        delegation_enabled=False,
        description="Worker with delegation disabled",
    )

    parent_llm = _scripted_parent_llm(
        target="worker",
        task="do the thing",
        child=_valid_child_response(status="success", summary="done"),
        final="The worker completed the task.",
    )

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    # El llm del worker NO se usa bajo C (el efímero hereda el del parent): sentinela.
    worker_container = _build_container(worker_cfg, global_cfg, _make_scripted_llm([]))

    # Bajo C el flag delegation del worker es IRRELEVANTE para ser target: el hijo efímero
    # se construye desde el delta (get_sub_agent_raw), no desde el run_agent_one_shot
    # pre-built del worker. _wire_both provee el get_sub_agent_raw del worker.
    _wire_both(parent_container, worker_container)

    # Assertion 3: el worker igual tiene run_agent_one_shot (se construye en __init__)
    assert hasattr(worker_container, "run_agent_one_shot"), (
        "worker must have run_agent_one_shot even with delegation.enabled=False"
    )
    assert isinstance(worker_container.run_agent_one_shot, RunAgentOneShotUseCase), (
        "worker.run_agent_one_shot must be a RunAgentOneShotUseCase instance"
    )

    # Assertion 4: el worker NO tiene 'delegate' en su registry (REQ-DG-1 preserved)
    assert "delegate" not in worker_container._tools._tools, (
        "worker must NOT have the 'delegate' tool when delegation.enabled=False (REQ-DG-1)"
    )

    # Assertion 1: parent.execute() no debe romper
    result = await parent_container.run_agent.execute("Delegate a task to worker")
    assert isinstance(result, str), "parent.execute() must return a string"

    # Assertion 2: DelegationResult success (última llamada del parent lleva el tool result)
    dr = DelegationResult.model_validate_json(
        parent_llm.complete.call_args_list[-1].args[0][-1].content
    )
    assert dr.status == "success", (
        f"DelegationResult must be status=success when child has delegation.enabled=False. "
        f"Got: {dr.status!r}, reason: {dr.reason!r}"
    )


# ===========================================================================
# T9 — Herencia per-delegación (el comportamiento central de C)
# ===========================================================================


async def test_same_sub_def_inherits_each_callers_llm():
    """
    Núcleo de C: la MISMA definición de sub-agente, delegada por P y por Q, hereda el
    LLM de CADA caller (no uno fijo). El hijo efímero corre sobre el ``_llm`` del parent;
    el ``_llm`` del container pre-built del sub NUNCA se usa (sentinela que lanza).
    """
    global_cfg = _make_global_config()
    sub_delta = {"id": "s", "name": "S", "description": "Sub compartido", "system_prompt": "Sos S."}
    s_cfg = _make_agent_config(agent_id="s", delegation_enabled=True, allowed_targets=[])

    # P delega S
    p_llm = _scripted_parent_llm(
        target="s", task="t", child=_valid_child_response(summary="from P"), final="P done"
    )
    p_cfg = _make_agent_config(agent_id="P", delegation_enabled=True, allowed_targets=["s"])
    p = _build_container(p_cfg, global_cfg, p_llm)
    s_for_p = _build_container(s_cfg, global_cfg, _make_scripted_llm([]))  # llm del sub: jamás usado
    _wire_both(p, s_for_p, sub_delta=sub_delta)
    await p.run_agent.execute("ask P")

    # Q delega la MISMA def S
    q_llm = _scripted_parent_llm(
        target="s", task="t", child=_valid_child_response(summary="from Q"), final="Q done"
    )
    q_cfg = _make_agent_config(agent_id="Q", delegation_enabled=True, allowed_targets=["s"])
    q = _build_container(q_cfg, global_cfg, q_llm)
    s_for_q = _build_container(s_cfg, global_cfg, _make_scripted_llm([]))
    _wire_both(q, s_for_q, sub_delta=sub_delta)
    await q.run_agent.execute("ask Q")

    # Cada caller sirvió el turno del hijo con SU propio llm (herencia per-caller).
    assert len(_child_turn_calls(p_llm)) == 1, "el llm de P debe haber servido el turno del hijo"
    assert len(_child_turn_calls(q_llm)) == 1, "el llm de Q debe haber servido el turno del hijo"


async def test_sub_llm_override_builds_new_llm_via_factory():
    """
    Si el delta del sub OVERRIDEA ``llm`` (≠ al del caller), el hijo NO hereda la instancia
    del parent: ``build_ephemeral_child`` construye una nueva vía ``LLMProviderFactory`` (con
    los providers heredados del caller). El parent_llm solo sirve los turnos del PARENT.
    """
    global_cfg = _make_global_config()
    sub_delta = {
        "id": "s",
        "name": "S",
        "description": "Sub con llm propio",
        "system_prompt": "Sos S.",
        "llm": {"model": "sub-distinct-model"},  # override → difiere del parent → factory
    }
    s_cfg = _make_agent_config(agent_id="s", delegation_enabled=True, allowed_targets=[])

    parent_llm = _make_scripted_llm([_tool_call_response("s", "t"), "P done"])  # solo turnos del parent
    override_llm = _make_scripted_llm([_valid_child_response(summary="from sub's own llm")])

    p_cfg = _make_agent_config(agent_id="P", delegation_enabled=True, allowed_targets=["s"])
    p = _build_container(p_cfg, global_cfg, parent_llm)
    s_container = _build_container(s_cfg, global_cfg, _make_scripted_llm([]))
    _wire_both(p, s_container, sub_delta=sub_delta)

    with patch(
        "infrastructure.container.LLMProviderFactory.create", return_value=override_llm
    ) as mock_create:
        await p.run_agent.execute("ask P")

    mock_create.assert_called_once()
    llm_arg, providers_arg = mock_create.call_args[0]
    assert llm_arg.model == "sub-distinct-model", "el factory recibe el llm overrideado"
    assert "openrouter" in providers_arg, "el hijo hereda el registry providers del caller"
    # El llm del override sirvió el turno del hijo; el parent_llm solo delegate + final.
    assert override_llm.complete.call_count == 1
    assert parent_llm.complete.call_count == 2


async def test_sub_allow_list_restricts_child_schemas():
    """
    ``tools.allowed`` en el delta del sub recorta los schemas que ve el hijo a ese subset
    del toolkit del CALLER (los recursos siguen siendo del caller). delegate se excluye igual.
    """
    global_cfg = _make_global_config()
    sub_delta = {
        "id": "s",
        "name": "S",
        "description": "Sub acotado",
        "system_prompt": "Sos S.",
        "tools": {"allowed": ["extra_a"]},  # solo extra_a, aunque el caller tenga más
    }
    s_cfg = _make_agent_config(agent_id="s", delegation_enabled=True, allowed_targets=[])

    parent_llm = _scripted_parent_llm(
        target="s", task="t", child=_valid_child_response(summary="ok"), final="done"
    )
    p_cfg = _make_agent_config(agent_id="P", delegation_enabled=True, allowed_targets=["s"])
    extra_a = _make_dummy_tool("extra_a")
    extra_b = _make_dummy_tool("extra_b")
    p = _build_container(p_cfg, global_cfg, parent_llm, extra_tools=[extra_a, extra_b])
    s_container = _build_container(s_cfg, global_cfg, _make_scripted_llm([]))
    _wire_both(p, s_container, sub_delta=sub_delta)

    await p.run_agent.execute("ask P")

    child_calls = _child_turn_calls(parent_llm)
    assert len(child_calls) == 1, f"Debe haber 1 turno del hijo. Hubo: {len(child_calls)}"
    names = {
        sch.get("function", {}).get("name") for sch in (child_calls[0].kwargs.get("tools") or [])
    }
    # El caller tiene dummy_tool + delegate + extra_a + extra_b; la allow-list recorta a extra_a.
    assert names == {"extra_a"}, f"la allow-list debe recortar a extra_a. Got: {names}"
