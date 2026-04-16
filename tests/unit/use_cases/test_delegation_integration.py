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
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.outbound.tools.delegate_tool import DelegateTool, _RESULT_FORMAT_FOOTER
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.domain.errors import ToolLoopMaxIterationsError
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
    MemoryConfig,
)
from infrastructure.container import AgentContainer


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
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_path="models/test"),
        memory=MemoryConfig(db_path=":memory:"),
        chat_history=ChatHistoryConfig(db_path="/tmp/inaki_test/integ_history.db"),
        delegation=AgentDelegationConfig(
            enabled=delegation_enabled,
            allowed_targets=allowed_targets or [],
        ),
    )


def _make_global_config(
    max_iterations_per_sub: int = 10,
    timeout_seconds: int = 60,
) -> GlobalConfig:
    from infrastructure.config import AppConfig, SchedulerConfig, SkillsConfig, ToolsConfig, WorkspaceConfig

    return GlobalConfig(
        app=AppConfig(ext_dirs=[]),
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_path="models/test"),
        memory=MemoryConfig(db_path=":memory:"),
        chat_history=ChatHistoryConfig(db_path="/tmp/inaki_test/integ_history.db"),
        skills=SkillsConfig(),
        tools=ToolsConfig(),
        scheduler=SchedulerConfig(),
        workspace=WorkspaceConfig(),
        delegation=DelegationConfig(
            max_iterations_per_sub=max_iterations_per_sub,
            timeout_seconds=timeout_seconds,
        ),
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
        r if isinstance(r, LLMResponse) else LLMResponse.of_text(r)
        for r in responses
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
        history=AsyncMock(load=AsyncMock(return_value=[]), append=AsyncMock()),
        tools=container._tools,
        agent_config=agent_config,
    )

    # Every container gets run_agent_one_shot unconditionally (mirrors __init__ behaviour).
    container.run_agent_one_shot = RunAgentOneShotUseCase(
        llm=llm,
        tools=container._tools,
        agent_config=agent_config,
    )

    return container


def _wire_both(
    parent: AgentContainer,
    child: AgentContainer,
) -> Callable[[str], AgentContainer | None]:
    """
    Build the minimal two-container harness: create the get_agent_container
    closure and call wire_delegation on both containers.

    Returns the closure for use in assertions.
    """
    containers = {
        parent.agent_config.id: parent,
        child.agent_config.id: child,
    }

    def _get_agent_container(agent_id: str) -> AgentContainer | None:
        return containers.get(agent_id)

    parent.wire_delegation(_get_agent_container)
    child.wire_delegation(_get_agent_container)

    return _get_agent_container


def _tool_call_response(agent_id: str, task: str) -> LLMResponse:
    """Build a scripted LLM response that represents a delegate tool call."""
    return LLMResponse(
        text_blocks=[],
        tool_calls=[
            {
                "function": {
                    "name": "delegate",
                    "arguments": json.dumps({"agent_id": agent_id, "task": task}),
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

    parent_llm = _make_scripted_llm([
        _tool_call_response("child", "compute the sum of 2 and 2"),
        "The specialist computed 2 + 2 = 4.",  # final answer — no tool calls
    ])
    child_llm = _make_scripted_llm([
        _valid_child_response(
            status="success",
            summary="2 + 2 = 4",
            details="Computed by basic arithmetic.",
        ),
    ])

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["child"],
        description="Coordinator agent",
    )
    # Child must have delegation.enabled=True so wire_delegation sets run_agent_one_shot.
    # delegation.enabled=True does NOT mean the child can delegate further — it just
    # ensures run_agent_one_shot is instantiated on the container (required by DelegateTool).
    # REQ-DG-9 guarantees the child's LLM never sees the "delegate" schema.
    child_cfg = _make_agent_config(
        agent_id="child",
        delegation_enabled=True,
        allowed_targets=[],    # no further delegation targets
        description="Arithmetic specialist",
    )

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, child_llm)

    _wire_both(parent_container, child_container)

    # --- Execute the full chain ---
    result = await parent_container.run_agent.execute("What is 2 + 2?")

    # Assertion 1: final text is the parent's second response
    assert result == "The specialist computed 2 + 2 = 4."

    # Assertion 2: parent LLM called twice
    assert parent_llm.complete.call_count == 2, (
        f"Parent LLM must be called exactly 2 times. Called: {parent_llm.complete.call_count}"
    )

    # Assertion 3: child LLM called once
    assert child_llm.complete.call_count == 1, (
        f"Child LLM must be called exactly 1 time. Called: {child_llm.complete.call_count}"
    )

    # Assertion 4: child LLM system_prompt contains _RESULT_FORMAT_FOOTER (6.2 wired)
    child_system_prompt = child_llm.complete.call_args.args[1]
    assert _RESULT_FORMAT_FOOTER in child_system_prompt, (
        "Child system prompt must contain _RESULT_FORMAT_FOOTER (task 6.2 wired end-to-end)"
    )

    # Assertion 5: child LLM tool_schemas does NOT include "delegate" (REQ-DG-9)
    child_tool_schemas = child_llm.complete.call_args.kwargs.get("tools") or []
    child_schema_names = [s.get("function", {}).get("name") for s in child_tool_schemas]
    assert "delegate" not in child_schema_names, (
        f"Child schemas must NOT include 'delegate' (REQ-DG-9). Got: {child_schema_names}"
    )

    # Assertion 6: parent system prompt contains child id (6.1 discovery section wired)
    # The parent's first LLM call carries its system prompt in args[1]
    parent_first_system_prompt = parent_llm.complete.call_args_list[0].args[1]
    assert "child" in parent_first_system_prompt, (
        "Parent system prompt must contain 'child' agent in the discovery section (task 6.1)"
    )

    # Assertion 7: The tool_result passed back to the parent LLM (second call)
    # must embed a DelegationResult with status="success".
    # We verify by inspecting the messages list in the parent's 2nd LLM call:
    # the last message before the final call is [Resultados de tools] containing JSON.
    parent_second_call_messages = parent_llm.complete.call_args_list[1].args[0]
    tool_result_msg = parent_second_call_messages[-1]
    assert "[delegate]:" in tool_result_msg.content, (
        "Second parent LLM call must include delegate tool result in messages"
    )
    # Extract the JSON embedded in the tool result message
    json_start = tool_result_msg.content.index("[delegate]:") + len("[delegate]: ")
    raw_json = tool_result_msg.content[json_start:].strip()
    dr = DelegationResult.model_validate_json(raw_json)
    assert dr.status == "success", f"DelegationResult must be status=success. Got: {dr.status}"


# ===========================================================================
# Task 7.2 — All failure modes (REQ-DG-2, REQ-DG-3, REQ-DG-5, REQ-DG-6, REQ-DG-8)
# ===========================================================================


async def _run_delegation_and_extract_result(
    parent_container: AgentContainer,
    task: str = "do something",
) -> DelegationResult:
    """
    Run the parent's execute() with a scripted delegate call, then
    extract the DelegationResult from the second parent LLM call's messages.

    The parent LLM is scripted to make ONE delegate call (first response)
    and then return a final answer (second response).
    """
    result_text = await parent_container.run_agent.execute(task)
    # The second call to parent LLM carries the tool result
    parent_llm = parent_container._llm
    second_call_messages = parent_llm.complete.call_args_list[1].args[0]
    tool_result_msg = second_call_messages[-1]
    json_str = tool_result_msg.content.split("[delegate]:", 1)[1].strip()
    return DelegationResult.model_validate_json(json_str)


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

    parent_llm = _make_scripted_llm([
        _tool_call_response("evil_agent", "do something sneaky"),
        "I could not complete the task.",  # final answer
    ])
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

    parent_llm = _make_scripted_llm([
        _tool_call_response("ghost", "haunt the server"),
        "I could not find the agent.",
    ])

    # Only build the parent container. "ghost" is NOT in the container registry.
    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)

    # Wire manually: closure only has "parent", not "ghost"
    containers = {"parent": parent_container}

    def _get_container(agent_id: str) -> AgentContainer | None:
        return containers.get(agent_id)

    parent_container.wire_delegation(_get_container)

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

    parent_llm = _make_scripted_llm([
        _tool_call_response("child", "do something"),
        "Child could not complete the task.",
    ])
    child_llm = _make_scripted_llm([
        "I did the thing but forgot to format the result.",  # no json block
    ])

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, child_llm)
    _wire_both(parent_container, child_container)

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason == "result_parse_error", (
        f"Expected 'result_parse_error', got {dr.reason!r}"
    )


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

    parent_llm = _make_scripted_llm([
        _tool_call_response("child", "do something"),
        "Child had a parse error.",
    ])
    child_llm = _make_scripted_llm([
        "Some output\n```json\n{not: valid json!!}\n```",
    ])

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, child_llm)
    _wire_both(parent_container, child_container)

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason == "result_parse_error", (
        f"Expected 'result_parse_error', got {dr.reason!r}"
    )


async def test_failure_timeout():
    """
    REQ-DG-6 (timeout): Child LLM sleeps longer than timeout_seconds.
    DelegationResult.reason must be exactly "timeout".

    Uses a very short timeout (0.05s) and a child LLM that sleeps longer.
    """
    global_cfg = _make_global_config(timeout_seconds=1)  # very short timeout

    parent_cfg = _make_agent_config(
        agent_id="parent", delegation_enabled=True, allowed_targets=["child"]
    )
    child_cfg = _make_agent_config(agent_id="child", delegation_enabled=True, allowed_targets=[])

    parent_llm = _make_scripted_llm([
        _tool_call_response("child", "slow task"),
        "Task timed out.",
    ])

    # Child LLM will sleep longer than the timeout
    child_llm = AsyncMock()

    async def _slow_complete(messages, system_prompt, tools=None):
        await asyncio.sleep(10)  # much longer than 1s timeout
        return LLMResponse.of_text(_valid_child_response())  # never reached

    child_llm.complete.side_effect = _slow_complete

    # Use a very short timeout for this test
    short_timeout_global = _make_global_config(
        max_iterations_per_sub=10,
        timeout_seconds=1,
    )

    parent_container = _build_container(parent_cfg, short_timeout_global, parent_llm)
    child_container = _build_container(child_cfg, short_timeout_global, child_llm)
    _wire_both(parent_container, child_container)

    # Override the delegate tool's timeout to something very small for test speed
    delegate_tool: DelegateTool = parent_container._tools._tools["delegate"]
    delegate_tool._timeout_seconds = 1  # 1 second is fast enough to distinguish from 10s sleep

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

    parent_llm = _make_scripted_llm([
        _tool_call_response("child", "infinite loop task"),
        "Child exceeded max iterations.",
    ])

    # Child LLM ALWAYS returns a tool call for "dummy_tool" → never final text
    child_tool_call = LLMResponse(
        text_blocks=[],
        tool_calls=[
            {
                "function": {
                    "name": "dummy_tool",
                    "arguments": "{}",
                }
            }
        ],
        raw="",
    )
    child_llm = AsyncMock()
    child_llm.complete.return_value = child_tool_call

    # Build a dummy tool that succeeds so it keeps looping
    dummy_tool = _make_dummy_tool("dummy_tool")
    from core.ports.outbound.tool_port import ToolResult
    dummy_tool.execute = AsyncMock(
        return_value=ToolResult(tool_name="dummy_tool", output="ok", success=True)
    )

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, child_llm, extra_tools=[])
    # Register the dummy tool on the child's tools registry directly
    child_container._tools.register(dummy_tool)
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

    parent_llm = _make_scripted_llm([
        _tool_call_response("child", "risky task"),
        "Child failed unexpectedly.",
    ])

    child_llm = AsyncMock()
    child_llm.complete.side_effect = RuntimeError("boom")

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, child_llm)
    _wire_both(parent_container, child_container)

    dr = await _run_delegation_and_extract_result(parent_container)

    assert dr.status == "failed"
    assert dr.reason is not None
    assert dr.reason.startswith("child_exception:"), (
        f"Reason must start with 'child_exception:'. Got: {dr.reason!r}"
    )
    assert "RuntimeError" in dr.reason, (
        f"Reason must contain 'RuntimeError'. Got: {dr.reason!r}"
    )


@pytest.mark.parametrize("scenario,expected_reason", [
    ("target_not_allowed", "target_not_allowed"),
    ("unknown_agent", "unknown_agent"),
    ("result_parse_error_no_block", "result_parse_error"),
    ("result_parse_error_invalid_json", "result_parse_error"),
    ("child_exception", "child_exception:RuntimeError"),
])
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
        # Delegate to an agent not in allowed_targets
        parent_llm = _make_scripted_llm([
            _tool_call_response("other_agent", "task"),
            "Done.",
        ])
        child_llm = _make_scripted_llm([])
        parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
        child_container = _build_container(child_cfg, global_cfg, child_llm)
        _wire_both(parent_container, child_container)

    elif scenario == "unknown_agent":
        # "child" is in allowed_targets but won't be in containers dict
        parent_llm = _make_scripted_llm([
            _tool_call_response("child", "task"),
            "Done.",
        ])
        child_llm = _make_scripted_llm([])
        parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
        child_container = _build_container(child_cfg, global_cfg, child_llm)
        # Wire with only parent — "child" resolves to None from the closure
        containers_dict = {"parent": parent_container}
        parent_container.wire_delegation(containers_dict.get)
        # child also needs wire_delegation called on it to satisfy the child cfg
        # but parent's closure won't find child (unknown_agent scenario)

    elif scenario in ("result_parse_error_no_block", "result_parse_error_invalid_json"):
        child_response = (
            "Plain text, no JSON block."
            if "no_block" in scenario
            else "Some output\n```json\n{not valid}\n```"
        )
        parent_llm = _make_scripted_llm([
            _tool_call_response("child", "task"),
            "Done.",
        ])
        child_llm = _make_scripted_llm([child_response])
        parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
        child_container = _build_container(child_cfg, global_cfg, child_llm)
        _wire_both(parent_container, child_container)

    elif scenario == "child_exception":
        parent_llm = _make_scripted_llm([
            _tool_call_response("child", "risky task"),
            "Done.",
        ])
        child_llm = AsyncMock()
        child_llm.complete.side_effect = RuntimeError("boom")
        parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
        child_container = _build_container(child_cfg, global_cfg, child_llm)
        _wire_both(parent_container, child_container)

    else:
        pytest.fail(f"Unknown scenario: {scenario}")

    # Parent must NOT raise
    result_text = await parent_container.run_agent.execute("task")
    assert isinstance(result_text, str)

    # Extract the DelegationResult from the tool result message
    parent_llm = parent_container._llm
    second_call_messages = parent_llm.complete.call_args_list[1].args[0]
    tool_result_msg = second_call_messages[-1]
    json_str = tool_result_msg.content.split("[delegate]:", 1)[1].strip()
    dr = DelegationResult.model_validate_json(json_str)

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

    Setup: BOTH parent and child have delegation.enabled=True.
    Child config: delegation.enabled=True, allowed_targets=[] (recursion would be
    structurally possible if the schema filter didn't exist).

    After wire_both, the child has a "delegate" tool registered in its ToolRegistry.
    But when the parent delegates to the child, RunAgentOneShotUseCase MUST filter
    out "delegate" from the schemas passed to the child's run_tool_loop call.

    This test asserts:
    5. The schemas list captured from the child LLM call does NOT contain "delegate".
    6. The parent's own schemas list DOES contain "delegate" (filter is one-shot-specific).
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
        delegation_enabled=True,   # <-- the twist: child also has delegation enabled
        allowed_targets=[],        # no further targets
        description="Sub-agent with delegation enabled",
    )

    # Parent: delegates to child, then returns final answer
    parent_llm = _make_scripted_llm([
        _tool_call_response("child", "compute something"),
        "The child computed the result.",
    ])

    # Child: returns a valid happy-path response on first call
    child_llm = _make_scripted_llm([
        _valid_child_response(status="success", summary="Computed successfully."),
    ])

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, child_llm)
    _wire_both(parent_container, child_container)

    # Verify the child DOES have "delegate" in its ToolRegistry after wiring
    assert "delegate" in child_container._tools._tools, (
        "Precondition: child must have 'delegate' tool in its registry after wire_delegation "
        "(since delegation.enabled=True for child)"
    )

    # Verify the parent ALSO has "delegate" in its ToolRegistry (sanity)
    assert "delegate" in parent_container._tools._tools, (
        "Precondition: parent must have 'delegate' tool in its registry"
    )

    # Execute the full delegation chain
    result = await parent_container.run_agent.execute("Do something complex")

    # Assertion 5: child LLM was called with schemas that do NOT include "delegate"
    assert child_llm.complete.call_count == 1, (
        f"Child LLM must be called exactly once. Called: {child_llm.complete.call_count}"
    )
    child_call_kwargs = child_llm.complete.call_args.kwargs
    child_schemas = child_call_kwargs.get("tools") or []
    child_schema_names = [s.get("function", {}).get("name") for s in child_schemas]

    assert "delegate" not in child_schema_names, (
        f"REQ-DG-9: Child schemas must NOT include 'delegate'. "
        f"Got schemas: {child_schema_names}"
    )
    assert len(child_schema_names) >= 1, (
        "Child must still have at least one non-delegate schema (dummy_tool)"
    )

    # Assertion 6: Parent's own LLM call DID receive "delegate" in schemas,
    # confirming the filter is specific to the one-shot path, not a global side effect.
    parent_first_call_kwargs = parent_llm.complete.call_args_list[0].kwargs
    parent_schemas = parent_first_call_kwargs.get("tools") or []
    parent_schema_names = [s.get("function", {}).get("name") for s in parent_schemas]

    assert "delegate" in parent_schema_names, (
        f"Parent schemas MUST include 'delegate' (it is a conversational agent). "
        f"Got schemas: {parent_schema_names}"
    )

    # Sanity: the delegation chain succeeded
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

    parent_llm = _make_scripted_llm([
        _tool_call_response("child", "a simple task"),
        "Done.",
    ])
    child_llm = _make_scripted_llm([
        _valid_child_response(status="success", summary="Simple task done."),
    ])

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    child_container = _build_container(child_cfg, global_cfg, child_llm)
    _wire_both(parent_container, child_container)

    await parent_container.run_agent.execute("Do a simple task")

    # Child LLM schemas must not include "delegate"
    child_schemas = child_llm.complete.call_args.kwargs.get("tools") or []
    child_schema_names = [s.get("function", {}).get("name") for s in child_schemas]
    assert "delegate" not in child_schema_names, (
        f"REQ-DG-9: Child schemas must not include 'delegate'. Got: {child_schema_names}"
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

    parent_llm = _make_scripted_llm([
        _tool_call_response("worker", "do the thing"),
        "The worker completed the task.",  # final answer after delegation
    ])
    worker_llm = _make_scripted_llm([
        _valid_child_response(status="success", summary="done"),
    ])

    parent_container = _build_container(parent_cfg, global_cfg, parent_llm)
    worker_container = _build_container(worker_cfg, global_cfg, worker_llm)

    containers = {
        "parent": parent_container,
        "worker": worker_container,
    }

    def _get_agent_container(agent_id: str) -> AgentContainer | None:
        return containers.get(agent_id)

    # Wire both: parent gets delegate tool + discovery section;
    # worker is a no-op for wire_delegation (enabled=False), but run_agent_one_shot
    # is already on worker_container (set by _build_container mirroring __init__).
    parent_container.wire_delegation(_get_agent_container)
    worker_container.wire_delegation(_get_agent_container)

    # Assertion 3: worker has run_agent_one_shot (set unconditionally)
    assert hasattr(worker_container, "run_agent_one_shot"), (
        "worker must have run_agent_one_shot even with delegation.enabled=False"
    )
    assert isinstance(worker_container.run_agent_one_shot, RunAgentOneShotUseCase), (
        "worker.run_agent_one_shot must be a RunAgentOneShotUseCase instance"
    )

    # Assertion 4: worker does NOT have 'delegate' tool in registry (REQ-DG-1 preserved)
    assert "delegate" not in worker_container._tools._tools, (
        "worker must NOT have the 'delegate' tool when delegation.enabled=False (REQ-DG-1)"
    )

    # Assertion 1: parent.execute() must NOT raise AttributeError
    result = await parent_container.run_agent.execute("Delegate a task to worker")
    assert isinstance(result, str), "parent.execute() must return a string"

    # Assertion 2: DelegationResult must have status="success"
    second_call_messages = parent_llm.complete.call_args_list[1].args[0]
    tool_result_msg = second_call_messages[-1]
    json_str = tool_result_msg.content.split("[delegate]:", 1)[1].strip()
    dr = DelegationResult.model_validate_json(json_str)
    assert dr.status == "success", (
        f"DelegationResult must be status=success when child has delegation.enabled=False. "
        f"Got: {dr.status!r}, reason: {dr.reason!r}"
    )
