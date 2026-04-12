"""
Unit tests for AgentContainer.wire_scheduler — idempotency and registration.

Coverage:
1. Call wire_scheduler() twice → assert tool executor contains exactly ONE "scheduler" entry
2. Call with None use case → assert no tool registered (no-op)
3. Call once → verify SchedulerTool is in the tool executor with correct agent_id
   and user_timezone

Follows the EXACT fixture/mock patterns from test_container.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.outbound.tools.scheduler_tool import SchedulerTool
from adapters.outbound.tools.tool_registry import ToolRegistry
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
from core.use_cases.schedule_task import ScheduleTaskUseCase


# ---------------------------------------------------------------------------
# Helpers — mirrors test_container.py pattern exactly
# ---------------------------------------------------------------------------

class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _make_agent_config(
    agent_id: str = "test-agent",
) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name=agent_id.capitalize(),
        description=f"Agent {agent_id}",
        system_prompt="Test prompt",
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_path="models/test"),
        memory=MemoryConfig(db_path=":memory:"),
        chat_history=ChatHistoryConfig(db_path="/tmp/inaki_test/history.db"),
        delegation=AgentDelegationConfig(
            enabled=False,
            allowed_targets=[],
        ),
    )


def _make_global_config() -> GlobalConfig:
    from infrastructure.config import (
        AppConfig,
        SchedulerConfig,
        SkillsConfig,
        ToolsConfig,
        WorkspaceConfig,
    )
    return GlobalConfig(
        app=AppConfig(ext_dirs=[]),
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_path="models/test"),
        memory=MemoryConfig(db_path=":memory:"),
        chat_history=ChatHistoryConfig(db_path="/tmp/inaki_test/history.db"),
        skills=SkillsConfig(),
        tools=ToolsConfig(),
        scheduler=SchedulerConfig(),
        workspace=WorkspaceConfig(),
        delegation=DelegationConfig(),
    )


def _build_minimal_container(
    agent_config: AgentConfig,
    global_config: GlobalConfig,
) -> AgentContainer:
    """
    Build an AgentContainer bypassing real IO — identical pattern to test_container.py.
    Uses __new__ + manual attribute injection.
    """
    container = AgentContainer.__new__(AgentContainer)
    container.agent_config = agent_config
    container._global_config = global_config
    container._delegation_wired = False
    container._scheduler_wired = False
    container._llm = AsyncMock()
    container._embedder = FakeEmbedder()
    container._tools = ToolRegistry(embedder=container._embedder)
    # Pre-register a dummy tool so the registry is non-empty (realistic)
    dummy_tool = MagicMock()
    dummy_tool.name = "dummy_tool"
    dummy_tool.description = "A dummy tool"
    dummy_tool.parameters_schema = {"type": "object", "properties": {}}
    container._tools.register(dummy_tool)
    # run_agent is needed (mirrors test_container.py)
    container.run_agent = MagicMock(spec=RunAgentUseCase)
    container.run_agent._extra_system_sections = []
    container.run_agent_one_shot = RunAgentOneShotUseCase(
        llm=container._llm,
        tools=container._tools,
        agent_config=agent_config,
    )
    return container


def _make_mock_use_case() -> MagicMock:
    """Returns a MagicMock that passes isinstance checks via spec."""
    uc = MagicMock(spec=ScheduleTaskUseCase)
    return uc


# ---------------------------------------------------------------------------
# Test 1 — Idempotency: wire_scheduler twice → exactly one "scheduler" entry
# ---------------------------------------------------------------------------

def test_wire_scheduler_idempotent() -> None:
    """
    Calling wire_scheduler twice must be a no-op the second time.
    The "scheduler" tool must appear exactly ONCE in the registry.
    """
    agent_cfg = _make_agent_config(agent_id="agent-x")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)
    uc = _make_mock_use_case()

    container.wire_scheduler(uc, "America/Argentina/Buenos_Aires")
    container.wire_scheduler(uc, "America/Argentina/Buenos_Aires")  # second call — must be no-op

    scheduler_names = [name for name in container._tools._tools if name == "scheduler"]
    assert len(scheduler_names) == 1, (
        f"scheduler tool must be registered exactly once, found {len(scheduler_names)}"
    )


# ---------------------------------------------------------------------------
# Test 2 — None use case → no-op (no tool registered)
# ---------------------------------------------------------------------------

def test_wire_scheduler_noop_when_use_case_is_none() -> None:
    """
    When schedule_task_uc is None, wire_scheduler must be a no-op.
    No 'scheduler' tool must be registered.
    """
    agent_cfg = _make_agent_config(agent_id="agent-y")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    container.wire_scheduler(None, "UTC")

    assert "scheduler" not in container._tools._tools, (
        "scheduler tool must NOT be registered when schedule_task_uc is None"
    )
    # Guard flag must remain False (no partial wiring)
    assert container._scheduler_wired is False


# ---------------------------------------------------------------------------
# Test 3 — Happy path: correct agent_id and user_timezone injected
# ---------------------------------------------------------------------------

def test_wire_scheduler_registers_tool_with_correct_config() -> None:
    """
    Single wire_scheduler call → SchedulerTool registered with the correct
    agent_id (from AgentConfig) and user_timezone (from the argument).
    """
    agent_cfg = _make_agent_config(agent_id="my-agent")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)
    uc = _make_mock_use_case()
    timezone = "Europe/Madrid"

    container.wire_scheduler(uc, timezone)

    assert "scheduler" in container._tools._tools, (
        "scheduler tool must be registered after wire_scheduler"
    )
    tool = container._tools._tools["scheduler"]
    assert isinstance(tool, SchedulerTool), (
        "registered tool must be a SchedulerTool instance"
    )
    assert tool._agent_id == "my-agent", (
        f"SchedulerTool._agent_id must be 'my-agent', got {tool._agent_id!r}"
    )
    assert tool._user_timezone == "Europe/Madrid", (
        f"SchedulerTool._user_timezone must be 'Europe/Madrid', got {tool._user_timezone!r}"
    )
    # Verify the use case reference is the one we passed
    assert tool._uc is uc


# ---------------------------------------------------------------------------
# Test 4 — Guard flag set after successful wiring
# ---------------------------------------------------------------------------

def test_wire_scheduler_sets_wired_flag() -> None:
    """
    After a successful wire_scheduler, _scheduler_wired must be True.
    Before wiring, it must be False.
    """
    agent_cfg = _make_agent_config(agent_id="agent-z")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)
    uc = _make_mock_use_case()

    assert container._scheduler_wired is False, "flag must start False"

    container.wire_scheduler(uc, "UTC")

    assert container._scheduler_wired is True, "flag must be True after wiring"


# ---------------------------------------------------------------------------
# Test 5 — None skips flag update
# ---------------------------------------------------------------------------

def test_wire_scheduler_none_does_not_set_flag() -> None:
    """
    When schedule_task_uc is None, _scheduler_wired must remain False.
    This ensures a subsequent call with a real use case still wires correctly.
    """
    agent_cfg = _make_agent_config(agent_id="agent-w")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    container.wire_scheduler(None, "UTC")
    assert container._scheduler_wired is False

    # Now wire with a real use case — must still work
    uc = _make_mock_use_case()
    container.wire_scheduler(uc, "UTC")
    assert container._scheduler_wired is True
    assert "scheduler" in container._tools._tools


# ---------------------------------------------------------------------------
# Test 6 — Idempotency with different arguments: second call ignored entirely
# ---------------------------------------------------------------------------

def test_wire_scheduler_idempotent_different_args() -> None:
    """
    Second call with a DIFFERENT timezone and use case must still be a no-op.
    The first call's agent_id and timezone are preserved.
    """
    agent_cfg = _make_agent_config(agent_id="agent-q")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)
    uc1 = _make_mock_use_case()
    uc2 = _make_mock_use_case()

    container.wire_scheduler(uc1, "UTC")
    container.wire_scheduler(uc2, "Asia/Tokyo")  # second call — must be no-op

    tool = container._tools._tools["scheduler"]
    # The FIRST call's use case and timezone are preserved
    assert tool._uc is uc1
    assert tool._user_timezone == "UTC"

    # Still exactly one "scheduler" entry
    scheduler_names = [n for n in container._tools._tools if n == "scheduler"]
    assert len(scheduler_names) == 1
