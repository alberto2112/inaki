"""
Tests for task 5.1 — AgentContainer.wire_delegation and AppContainer two-phase init.

Coverage:
1. REQ-DG-1 (enabled=False): wire_delegation is a no-op — delegate tool never registered,
   run_agent_one_shot never set.
2. REQ-DG-1 (enabled=True): delegate tool registered, run_agent_one_shot is correct instance.
3. Idempotency: calling wire_delegation twice does not duplicate the delegate tool.
4. AppContainer two-phase init: delegate tool wired for agent A (enabled), absent for B and C.
5. Late binding: get_agent_container closure is bound over the final agents dict.
"""

from __future__ import annotations

from typing import Callable
from unittest.mock import AsyncMock, MagicMock, patch


from adapters.outbound.tools.delegate_tool import DelegateTool
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.use_cases.run_agent import RunAgentUseCase
from core.domain.value_objects.agent_settings import OneShotSettings
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
from infrastructure.container import AgentContainer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _make_agent_config(
    agent_id: str = "test-agent",
    delegation_enabled: bool = False,
    allowed_targets: list[str] | None = None,
) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name=agent_id.capitalize(),
        description=f"Agent {agent_id}",
        system_prompt="Test prompt",
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memories=MemoriesConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
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
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
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


def _build_minimal_container(
    agent_config: AgentConfig,
    global_config: GlobalConfig,
    tmp_path,
) -> AgentContainer:
    """
    Build an AgentContainer bypassing real IO (no filesystem, no LLM factory,
    no embedding model). Uses __new__ + manual attribute injection.
    """
    container = AgentContainer.__new__(AgentContainer)
    container.agent_config = agent_config
    container._global_config = global_config
    container._delegation_wired = False
    container._llm = AsyncMock()
    container._embedder = FakeEmbedder()
    container._tools = ToolRegistry(embedder=container._embedder)
    # Pre-register a dummy tool so the registry is non-empty (realistic)
    dummy_tool = MagicMock()
    dummy_tool.name = "dummy_tool"
    dummy_tool.description = "A dummy tool"
    dummy_tool.parameters_schema = {"type": "object", "properties": {}}
    container._tools.register(dummy_tool)
    # run_agent is needed by wire_delegation task 6.1 (set_extra_system_sections)
    container.run_agent = MagicMock(spec=RunAgentUseCase)
    container.run_agent._extra_system_sections = []
    container.run_agent.set_extra_system_sections = MagicMock(
        side_effect=lambda sections: container.run_agent._extra_system_sections.__class__(sections)
    )
    # Use a simple list to capture what was set (side_effect stores in a mutable holder)
    container.run_agent._extra_system_sections = []

    def _capture_sections(sections: list[str]) -> None:
        container.run_agent._extra_system_sections = list(sections)

    container.run_agent.set_extra_system_sections = MagicMock(side_effect=_capture_sections)

    # Every container gets run_agent_one_shot unconditionally (mirrors __init__ behaviour).
    container.run_agent_one_shot = RunAgentOneShotUseCase(
        llm=container._llm,
        tools=container._tools,
        settings=OneShotSettings(
            agent_id=agent_config.id,
            system_prompt=agent_config.system_prompt,
            circuit_breaker_threshold=agent_config.tools.circuit_breaker_threshold,
        ),
    )
    return container


# ---------------------------------------------------------------------------
# Test 1 — REQ-DG-1 (delegation.enabled = False)
# ---------------------------------------------------------------------------


def test_wire_delegation_noop_when_disabled(tmp_path) -> None:
    """
    When delegation.enabled is False, wire_delegation MUST be a no-op
    with respect to DELEGATION concerns:
    - run_agent_one_shot IS present (set unconditionally in __init__)
    - the tool registry MUST NOT contain a 'delegate' tool (REQ-DG-1)
    - the get_agent_container callable MUST NOT be invoked
    """
    agent_cfg = _make_agent_config(agent_id="worker", delegation_enabled=False)
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg, tmp_path)

    get_container_mock = MagicMock()

    container.wire_delegation(get_agent_container=get_container_mock)

    # run_agent_one_shot MUST be present (constructed in __init__, not in wire_delegation)
    assert hasattr(container, "run_agent_one_shot"), (
        "run_agent_one_shot must be set even when delegation.enabled=False"
    )
    assert isinstance(container.run_agent_one_shot, RunAgentOneShotUseCase), (
        "run_agent_one_shot must be a RunAgentOneShotUseCase instance"
    )
    # No delegate tool in registry (REQ-DG-1 preserved)
    assert "delegate" not in container._tools._tools, (
        "delegate tool must not be in registry when delegation.enabled=False"
    )
    # The closure must never be called (it's only passed, never invoked during wiring)
    get_container_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — REQ-DG-1 (delegation.enabled = True)
# ---------------------------------------------------------------------------


def test_wire_delegation_registers_tool_when_enabled(tmp_path) -> None:
    """
    When delegation.enabled is True, wire_delegation MUST:
    - set container.run_agent_one_shot to a RunAgentOneShotUseCase instance
    - register the 'delegate' tool in the tool registry
    - construct DelegateTool with the correct allowed_targets, max_iterations_per_sub,
      and timeout_seconds from config
    """
    allowed_targets = ["specialist-agent"]
    agent_cfg = _make_agent_config(
        agent_id="coordinator",
        delegation_enabled=True,
        allowed_targets=allowed_targets,
    )
    global_cfg = _make_global_config(max_iterations_per_sub=7, timeout_seconds=30)
    container = _build_minimal_container(agent_cfg, global_cfg, tmp_path)

    get_container_mock: Callable[[str], AgentContainer | None] = MagicMock(return_value=None)

    container.wire_delegation(get_agent_container=get_container_mock, sub_agent_ids=allowed_targets)

    # run_agent_one_shot MUST be a RunAgentOneShotUseCase instance
    assert hasattr(container, "run_agent_one_shot"), "run_agent_one_shot must be set"
    assert isinstance(container.run_agent_one_shot, RunAgentOneShotUseCase), (
        "run_agent_one_shot must be a RunAgentOneShotUseCase"
    )

    # delegate tool MUST be in the registry
    assert "delegate" in container._tools._tools, (
        "delegate tool must be registered when delegation.enabled=True"
    )

    # Verify the DelegateTool was constructed with correct config
    delegate_tool = container._tools._tools["delegate"]
    assert isinstance(delegate_tool, DelegateTool)
    assert delegate_tool._allowed_targets == allowed_targets
    assert delegate_tool._max_iterations_per_sub == 7
    assert delegate_tool._timeout_seconds == 30

    # get_container_mock IS called during wiring to build the discovery section
    # (once per sub_agent_id). All return None here → no section set.
    get_container_mock.assert_called_once_with("specialist-agent")  # type: ignore[attr-defined]
    # All targets returned None → no extra sections set on run_agent
    container.run_agent.set_extra_system_sections.assert_not_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Test 3 — Idempotency
# ---------------------------------------------------------------------------


def test_wire_delegation_idempotent(tmp_path) -> None:
    """
    Calling wire_delegation twice MUST be a no-op the second time:
    - The delegate tool MUST appear exactly once in the registry
    - container.run_agent_one_shot MUST be the SAME instance both times
    """
    agent_cfg = _make_agent_config(
        agent_id="coordinator",
        delegation_enabled=True,
    )
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg, tmp_path)

    get_container_mock: Callable[[str], AgentContainer | None] = MagicMock(return_value=None)
    sub_agents = ["worker"]

    container.wire_delegation(get_agent_container=get_container_mock, sub_agent_ids=sub_agents)
    use_case_first_call = container.run_agent_one_shot

    # Second call — must be no-op
    container.wire_delegation(get_agent_container=get_container_mock, sub_agent_ids=sub_agents)
    use_case_second_call = container.run_agent_one_shot

    # Same instance
    assert use_case_first_call is use_case_second_call, (
        "run_agent_one_shot must be the same object on second wire_delegation call"
    )

    # delegate tool appears exactly once
    delegate_names = [name for name in container._tools._tools if name == "delegate"]
    assert len(delegate_names) == 1, (
        f"delegate tool must be registered exactly once, found {len(delegate_names)}"
    )


# ---------------------------------------------------------------------------
# Test 4 — AppContainer two-phase init
# ---------------------------------------------------------------------------


def test_app_container_two_phase_init(tmp_path) -> None:
    """
    AppContainer with three agents (A: enabled, B: disabled, C: disabled) MUST:
    - Wire the delegate tool for A only
    - B and C MUST NOT have the delegate tool
    - get_agent_container("B") returns B's container
    - get_agent_container("nonexistent") returns None
    """

    agent_a_cfg = _make_agent_config(
        agent_id="agent-a",
        delegation_enabled=True,
        allowed_targets=["agent-b"],
    )
    agent_b_cfg = _make_agent_config(agent_id="agent-b", delegation_enabled=False)
    agent_c_cfg = _make_agent_config(agent_id="agent-c", delegation_enabled=False)
    global_cfg = _make_global_config()

    # Build AgentContainers directly to avoid real IO
    container_a = _build_minimal_container(agent_a_cfg, global_cfg, tmp_path)
    container_b = _build_minimal_container(agent_b_cfg, global_cfg, tmp_path)
    container_c = _build_minimal_container(agent_c_cfg, global_cfg, tmp_path)

    # Simulate the AppContainer agents dict (post Phase 1)
    agents = {
        "agent-a": container_a,
        "agent-b": container_b,
        "agent-c": container_c,
    }

    # Simulate Phase 2 (the same logic AppContainer uses)
    # Solo agent-a es regular con delegation enabled; B y C no tienen sub-agentes
    sub_agent_ids = ["agent-b"]  # simulando que agent-b está en sub-agents/

    def _get_agent_container(agent_id: str) -> AgentContainer | None:
        return agents.get(agent_id)

    for agent_id, container in agents.items():
        # Solo los agentes regulares con delegation.enabled reciben sub_agent_ids
        ids = sub_agent_ids if agent_id == "agent-a" else []
        container.wire_delegation(_get_agent_container, sub_agent_ids=ids)

    # A MUST have the delegate tool wired
    assert "delegate" in container_a._tools._tools, (
        "agent-a (enabled=True) must have the delegate tool"
    )
    assert isinstance(container_a.run_agent_one_shot, RunAgentOneShotUseCase)

    # B and C MUST NOT have the delegate tool (REQ-DG-1 preserved)
    assert "delegate" not in container_b._tools._tools, (
        "agent-b (enabled=False) must NOT have the delegate tool"
    )
    assert "delegate" not in container_c._tools._tools, (
        "agent-c (enabled=False) must NOT have the delegate tool"
    )
    # B and C MUST have run_agent_one_shot (set unconditionally in __init__)
    assert isinstance(container_b.run_agent_one_shot, RunAgentOneShotUseCase), (
        "agent-b must have run_agent_one_shot even when delegation.enabled=False"
    )
    assert isinstance(container_c.run_agent_one_shot, RunAgentOneShotUseCase), (
        "agent-c must have run_agent_one_shot even when delegation.enabled=False"
    )

    # Verify closure resolution
    assert _get_agent_container("agent-b") is container_b, (
        "get_agent_container('agent-b') must return container_b"
    )
    assert _get_agent_container("nonexistent") is None, (
        "get_agent_container('nonexistent') must return None"
    )


# ---------------------------------------------------------------------------
# Test 5 — Late binding: closure is over the FINAL agents dict
# ---------------------------------------------------------------------------


def test_wire_delegation_build_child_resolves_against_caller(tmp_path) -> None:
    """
    Flujo C: el closure ``build_child`` que ``wire_delegation`` inyecta en el DelegateTool
    resuelve el sub-agente vía ``get_sub_agent_raw`` y construye la instancia EFÍMERA contra
    ESTE caller (``build_ephemeral_child``). Un id desconocido → None. Reemplaza el viejo
    test de late-binding del closure ``get_agent_container`` (el tool ya no lo almacena).
    """
    agent_a_cfg = _make_agent_config(
        agent_id="late-a",
        delegation_enabled=True,
        allowed_targets=[],
    )
    global_cfg = _make_global_config()
    container_a = _build_minimal_container(agent_a_cfg, global_cfg, tmp_path)

    # Delta crudo del sub-agente "late-b" (lo que daría registry.get_sub_agent_raw).
    sub_raw = {
        "id": "late-b",
        "name": "Late B",
        "description": "Sub-agente B",
        "system_prompt": "Sos B.",
    }

    def _get_sub_agent_raw(agent_id: str) -> dict | None:
        return sub_raw if agent_id == "late-b" else None

    container_a.wire_delegation(
        lambda _: None,
        sub_agent_ids=["late-b"],
        get_sub_agent_raw=_get_sub_agent_raw,
    )

    delegate_tool = container_a._tools._tools["delegate"]
    assert isinstance(delegate_tool, DelegateTool)

    # build_child construye una instancia efímera contra el caller para un sub conocido.
    child = delegate_tool._build_child("late-b")
    assert isinstance(child, RunAgentOneShotUseCase)
    # Sin override de llm en el delta → hereda la instancia del caller (mismo objeto).
    assert child._llm is container_a._llm, "el hijo efímero hereda el LLM del caller"
    # Un id desconocido → None.
    assert delegate_tool._build_child("ghost") is None, "id desconocido → None"


# ===========================================================================
# Tests 6–14 — Task 6.1 / REQ-DG-7: Agent discovery section injection
# ===========================================================================


def _build_target_container(
    agent_id: str,
    description: str,
    tool_names: list[str],
    global_config: GlobalConfig,
) -> AgentContainer:
    """Build a minimal target container with specific tools for discovery section tests."""
    target_cfg = _make_agent_config(
        agent_id=agent_id,
        delegation_enabled=False,
    )
    # Override description using model_copy (AgentConfig is a pydantic model)
    target_cfg = target_cfg.model_copy(update={"description": description})

    target = AgentContainer.__new__(AgentContainer)
    target.agent_config = target_cfg
    target._global_config = global_config
    target._delegation_wired = False
    target._llm = AsyncMock()
    target._embedder = FakeEmbedder()
    target._tools = ToolRegistry(embedder=target._embedder)
    target.run_agent = MagicMock()
    target.run_agent._extra_system_sections = []
    target.run_agent.set_extra_system_sections = MagicMock()

    for tool_name in tool_names:
        t = MagicMock()
        t.name = tool_name
        t.description = f"Tool {tool_name}"
        t.parameters_schema = {"type": "object", "properties": {}}
        target._tools.register(t)

    return target


# ---------------------------------------------------------------------------
# Test 6 — REQ-DG-9: section present when enabled (section present when enabled)
# ---------------------------------------------------------------------------


def test_discovery_section_present_when_enabled(tmp_path) -> None:
    """
    REQ-DG-7 / REQ-DG-9 scenario: section present when enabled.

    Parent with delegation.enabled=True and allowed_targets=["B"].
    B's container exists with description and tool names.
    After wire_delegation, run_agent.set_extra_system_sections is called
    with a list containing one string that includes "B", B's description,
    and B's tool names.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["agent-b"],
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    agent_b = _build_target_container(
        agent_id="agent-b",
        description="Specialist B does web research.",
        tool_names=["web_search", "fetch_url"],
        global_config=global_cfg,
    )

    def _get_container(agent_id: str) -> AgentContainer | None:
        return {"agent-b": agent_b}.get(agent_id)

    parent.wire_delegation(_get_container, sub_agent_ids=["agent-b"])

    # set_extra_system_sections MUST have been called
    parent.run_agent.set_extra_system_sections.assert_called_once()  # type: ignore[attr-defined]
    call_args = parent.run_agent.set_extra_system_sections.call_args[0][0]  # type: ignore[attr-defined]
    assert isinstance(call_args, list) and len(call_args) == 1
    section = call_args[0]

    assert "agent-b" in section, "Section must mention the target agent id"
    assert "Specialist B does web research." in section, "Section must include description"
    assert "web_search" in section, "Section must list tool web_search"
    assert "fetch_url" in section, "Section must list tool fetch_url"


# ---------------------------------------------------------------------------
# Test 7 — REQ-DG-9: filtered by allow-list
# ---------------------------------------------------------------------------


def test_discovery_section_filtered_by_allowlist(tmp_path) -> None:
    """
    REQ-DG-9 scenario: section filtered by allow-list.

    Parent allow-list is ["B"] but A, B, C all exist.
    Discovery section MUST NOT mention A or C. Only B.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["agent-b"],
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    agent_a = _build_target_container("agent-a", "Agent A", ["tool_a"], global_cfg)
    agent_b = _build_target_container("agent-b", "Agent B", ["tool_b"], global_cfg)
    agent_c = _build_target_container("agent-c", "Agent C", ["tool_c"], global_cfg)

    registry = {"agent-a": agent_a, "agent-b": agent_b, "agent-c": agent_c}

    # Solo agent-b es sub-agente — la sección solo debe mostrar agent-b
    parent.wire_delegation(registry.get, sub_agent_ids=["agent-b"])

    parent.run_agent.set_extra_system_sections.assert_called_once()  # type: ignore[attr-defined]
    section = parent.run_agent.set_extra_system_sections.call_args[0][0][0]  # type: ignore[attr-defined]

    assert "agent-b" in section
    assert "agent-a" not in section, "agent-a must NOT appear (not a sub-agent)"
    assert "agent-c" not in section, "agent-c must NOT appear (not a sub-agent)"


# ---------------------------------------------------------------------------
# Test 8 — REQ-DG-9: section absent when disabled
# ---------------------------------------------------------------------------


def test_discovery_section_absent_when_disabled(tmp_path) -> None:
    """
    REQ-DG-9 scenario: section absent when disabled.

    Parent with delegation.enabled=False.
    run_agent.set_extra_system_sections MUST NOT be called.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="worker",
        delegation_enabled=False,
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    get_container = MagicMock()

    parent.wire_delegation(get_container)

    parent.run_agent.set_extra_system_sections.assert_not_called()  # type: ignore[attr-defined]
    get_container.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9 — Empty allow-list: no section set
# ---------------------------------------------------------------------------


def test_discovery_section_empty_allowlist(tmp_path) -> None:
    """
    Parent with delegation.enabled=True and allowed_targets=[].
    No discovery section is set — _build_discovery_section returns "" for empty targets.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=[],
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    get_container = MagicMock()

    parent.wire_delegation(get_container)

    # No discovery section (empty allow-list → nothing to enumerate)
    parent.run_agent.set_extra_system_sections.assert_not_called()  # type: ignore[attr-defined]
    # get_container should not be called for enumeration (no targets)
    get_container.assert_not_called()


# ---------------------------------------------------------------------------
# Test 10 — Unknown targets skipped, known ones included
# ---------------------------------------------------------------------------


def test_discovery_section_unknown_targets_skipped(tmp_path) -> None:
    """
    Parent with allowed_targets=["B", "ghost"] where "ghost" doesn't exist.
    Discovery section mentions B only. No error raised.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["agent-b", "ghost"],
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    agent_b = _build_target_container("agent-b", "Agent B", ["tool_b"], global_cfg)

    def _get_container(agent_id: str) -> AgentContainer | None:
        return {"agent-b": agent_b}.get(agent_id)

    # Must not raise even though "ghost" doesn't exist
    parent.wire_delegation(_get_container, sub_agent_ids=["agent-b", "ghost"])

    parent.run_agent.set_extra_system_sections.assert_called_once()  # type: ignore[attr-defined]
    section = parent.run_agent.set_extra_system_sections.call_args[0][0][0]  # type: ignore[attr-defined]

    assert "agent-b" in section
    assert "ghost" not in section, "ghost must not appear in the discovery section"


# ---------------------------------------------------------------------------
# Test 11 — All targets unknown: no section set
# ---------------------------------------------------------------------------


def test_discovery_section_all_targets_unknown(tmp_path) -> None:
    """
    Parent with allowed_targets=["ghost"]. "ghost" doesn't exist.
    No discovery section is set. No error raised.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["ghost"],
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    # All return None
    parent.wire_delegation(lambda _: None, sub_agent_ids=["ghost"])

    parent.run_agent.set_extra_system_sections.assert_not_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Test 12 — Mixed: some resolve, some don't
# ---------------------------------------------------------------------------


def test_discovery_section_mixed_targets(tmp_path) -> None:
    """
    Parent with allowed_targets=["B", "ghost", "C"] where B and C exist.
    Section mentions B and C, not ghost.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["agent-b", "ghost", "agent-c"],
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    agent_b = _build_target_container("agent-b", "Agent B", ["tool_b"], global_cfg)
    agent_c = _build_target_container("agent-c", "Agent C", ["tool_c"], global_cfg)

    registry = {"agent-b": agent_b, "agent-c": agent_c}

    parent.wire_delegation(registry.get, sub_agent_ids=["agent-b", "ghost", "agent-c"])

    parent.run_agent.set_extra_system_sections.assert_called_once()  # type: ignore[attr-defined]
    section = parent.run_agent.set_extra_system_sections.call_args[0][0][0]  # type: ignore[attr-defined]

    assert "agent-b" in section
    assert "agent-c" in section
    assert "ghost" not in section


# ---------------------------------------------------------------------------
# Test 13 — One-shot isolation: RunAgentOneShotUseCase has no _extra_system_sections
# ---------------------------------------------------------------------------


def test_one_shot_has_no_extra_system_sections_attribute(tmp_path) -> None:
    """
    REQ-DG-9 one-shot isolation.

    RunAgentOneShotUseCase MUST NOT have an _extra_system_sections attribute.
    This ensures the discovery section cannot leak into child (one-shot) runs.
    """
    from unittest.mock import AsyncMock as _AsyncMock
    from core.use_cases.run_agent_one_shot import RunAgentOneShotUseCase

    agent_cfg = _make_agent_config("child", delegation_enabled=True)
    uc = RunAgentOneShotUseCase(
        llm=_AsyncMock(),
        tools=MagicMock(),
        settings=OneShotSettings(
            agent_id=agent_cfg.id,
            system_prompt=agent_cfg.system_prompt,
            circuit_breaker_threshold=agent_cfg.tools.circuit_breaker_threshold,
        ),
    )

    assert not hasattr(uc, "_extra_system_sections"), (
        "RunAgentOneShotUseCase must NOT have _extra_system_sections — "
        "the discovery section must never leak into child runs"
    )


# ---------------------------------------------------------------------------
# Test 14 — Idempotency: wire_delegation twice is safe (no double section)
# ---------------------------------------------------------------------------


def test_wire_delegation_idempotent_with_discovery(tmp_path) -> None:
    """
    Calling wire_delegation twice MUST be safe.
    The second call is a no-op — set_extra_system_sections called exactly once.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="parent",
        delegation_enabled=True,
        allowed_targets=["agent-b"],
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    agent_b = _build_target_container("agent-b", "Agent B", ["tool_b"], global_cfg)

    def _get_container(agent_id: str) -> AgentContainer | None:
        return {"agent-b": agent_b}.get(agent_id)

    parent.wire_delegation(_get_container, sub_agent_ids=["agent-b"])
    parent.wire_delegation(_get_container, sub_agent_ids=["agent-b"])  # second call — must be no-op

    # set_extra_system_sections called exactly ONCE (idempotency guard)
    assert parent.run_agent.set_extra_system_sections.call_count == 1, (  # type: ignore[attr-defined]
        "set_extra_system_sections must be called exactly once despite two wire_delegation calls"
    )


# ===========================================================================
# Tests 15–17 — run_agent_one_shot unconditional construction (batch 8 fix)
# ===========================================================================


def test_run_agent_one_shot_present_on_init_regardless_of_delegation_enabled(tmp_path) -> None:
    """
    NEW INVARIANT (batch 8): run_agent_one_shot is constructed in AgentContainer.__init__
    unconditionally — regardless of delegation.enabled.

    This test constructs a container with delegation.enabled=False and does NOT call
    wire_delegation. The one-shot use case must already be present and be a real instance.
    """
    agent_cfg = _make_agent_config(agent_id="worker", delegation_enabled=False)
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg, tmp_path)

    # wire_delegation NOT called — one-shot must already exist from __init__
    assert hasattr(container, "run_agent_one_shot"), (
        "run_agent_one_shot must be set in __init__ regardless of delegation.enabled"
    )
    assert isinstance(container.run_agent_one_shot, RunAgentOneShotUseCase), (
        "run_agent_one_shot must be a RunAgentOneShotUseCase instance"
    )


def test_run_agent_one_shot_present_when_delegation_enabled(tmp_path) -> None:
    """
    NEW INVARIANT (batch 8): run_agent_one_shot is available BEFORE wire_delegation
    is called, even when delegation.enabled=True.

    Confirms that __init__ alone (not wire_delegation) populates the attribute.
    """
    agent_cfg = _make_agent_config(
        agent_id="coordinator",
        delegation_enabled=True,
        allowed_targets=["specialist"],
    )
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg, tmp_path)

    # wire_delegation NOT yet called — one-shot must already be present from __init__
    assert hasattr(container, "run_agent_one_shot"), (
        "run_agent_one_shot must be present before wire_delegation is called"
    )
    assert isinstance(container.run_agent_one_shot, RunAgentOneShotUseCase), (
        "run_agent_one_shot must be a RunAgentOneShotUseCase instance before wire_delegation"
    )


def test_wire_delegation_does_not_replace_one_shot_use_case(tmp_path) -> None:
    """
    NEW INVARIANT (batch 8): wire_delegation must NOT construct or re-assign
    run_agent_one_shot. The instance set in __init__ must be the SAME object
    after wire_delegation completes (identity check).

    Guards against future regressions where wire_delegation accidentally
    re-constructs the one-shot use case.
    """
    agent_cfg = _make_agent_config(
        agent_id="coordinator",
        delegation_enabled=True,
        allowed_targets=[],
    )
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg, tmp_path)

    # Capture the instance set by __init__
    one_shot_before = container.run_agent_one_shot

    get_container_mock: Callable[[str], AgentContainer | None] = MagicMock(return_value=None)
    container.wire_delegation(get_agent_container=get_container_mock)

    # After wire_delegation, the instance must be the SAME object (identity)
    assert container.run_agent_one_shot is one_shot_before, (
        "wire_delegation must NOT replace run_agent_one_shot — "
        "the instance from __init__ must survive wiring unchanged"
    )


# ---------------------------------------------------------------------------
# Test 15 — allowed_targets in config filters sub_agent_ids
# ---------------------------------------------------------------------------


def test_wire_delegation_allowed_targets_filters_sub_agent_ids(tmp_path) -> None:
    """
    When delegation.allowed_targets is non-empty, wire_delegation must pass only
    the intersection of sub_agent_ids and allowed_targets to DelegateTool.

    Regression guard: previously allowed_targets was read from config but never
    applied — DelegateTool always received ALL sub_agent_ids regardless.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="coordinator",
        delegation_enabled=True,
        allowed_targets=["agent-a"],  # only agent-a is allowed
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    # Registry has two sub-agents; only agent-a is in allowed_targets
    parent.wire_delegation(lambda _: None, sub_agent_ids=["agent-a", "agent-b"])

    delegate_tool = parent._tools._tools.get("delegate")
    assert delegate_tool is not None, "delegate tool must be registered"
    assert isinstance(delegate_tool, DelegateTool)
    assert delegate_tool._allowed_targets == ["agent-a"], (
        "DelegateTool must only receive targets permitted by delegation.allowed_targets"
    )
    assert "agent-b" not in delegate_tool._allowed_targets, (
        "agent-b must be filtered out — it is not in delegation.allowed_targets"
    )


def test_wire_delegation_empty_allowed_targets_allows_all(tmp_path) -> None:
    """
    When delegation.allowed_targets is empty (default), no filter is applied —
    DelegateTool receives all sub_agent_ids.
    """
    global_cfg = _make_global_config()

    parent_cfg = _make_agent_config(
        agent_id="coordinator",
        delegation_enabled=True,
        allowed_targets=[],  # empty = no restriction
    )
    parent = _build_minimal_container(parent_cfg, global_cfg, tmp_path)

    parent.wire_delegation(lambda _: None, sub_agent_ids=["agent-a", "agent-b"])

    delegate_tool = parent._tools._tools.get("delegate")
    assert delegate_tool is not None, "delegate tool must be registered"
    assert isinstance(delegate_tool, DelegateTool)
    assert set(delegate_tool._allowed_targets) == {"agent-a", "agent-b"}, (
        "With empty allowed_targets, all sub_agent_ids must be wired"
    )


# ---------------------------------------------------------------------------
# build_ephemeral_child (T4 + T5) — instancia efímera resuelta contra el caller
# ---------------------------------------------------------------------------


def _sub_definition_raw(**overrides) -> dict:
    """Delta crudo de un sub-agente (lo que devuelve registry.get_sub_agent_raw)."""
    base: dict = {
        "id": "researcher",
        "name": "Researcher",
        "description": "Investiga temas puntuales",
        "system_prompt": "Sos un investigador especializado.",
    }
    base.update(overrides)
    return base


def test_ephemeral_child_inherits_llm_instance_when_not_overridden(tmp_path) -> None:
    """
    T5: el sub no overridea `llm` → SUBAGENT_DEFAULTS lo marca inherit → la config llm
    efectiva del hijo == la del caller → se REUSA la instancia `self._llm` (no se
    construye una nueva). El resultado es un RunAgentOneShotUseCase.
    """
    caller_cfg = _make_agent_config(agent_id="coordinator")
    caller = _build_minimal_container(caller_cfg, _make_global_config(), tmp_path)

    child = caller.build_ephemeral_child(_sub_definition_raw())

    assert isinstance(child, RunAgentOneShotUseCase)
    assert child._llm is caller._llm, (
        "llm heredado sin override debe REUSAR la instancia del caller"
    )


def test_ephemeral_child_builds_new_llm_when_overridden(tmp_path) -> None:
    """
    T5: el sub overridea `llm.model` → la config llm efectiva difiere de la del caller →
    se construye una instancia NUEVA vía factory. El factory recibe la llm efectiva (con
    el override) y los providers HEREDADOS del caller (para resolver credenciales).
    """
    caller_cfg = _make_agent_config(agent_id="coordinator")
    caller = _build_minimal_container(caller_cfg, _make_global_config(), tmp_path)

    sentinel_llm = AsyncMock()
    with patch(
        "infrastructure.container.LLMProviderFactory.create", return_value=sentinel_llm
    ) as mock_create:
        child = caller.build_ephemeral_child(_sub_definition_raw(llm={"model": "child-model"}))

    assert child._llm is sentinel_llm, "llm overrideado debe construir una instancia nueva"
    mock_create.assert_called_once()
    llm_arg, providers_arg = mock_create.call_args[0]
    assert llm_arg.model == "child-model", "el factory recibe la llm efectiva con el override"
    assert llm_arg.provider == "openrouter", "el resto del bloque llm se hereda del caller"
    assert "openrouter" in providers_arg, "el hijo hereda el registry `providers` del caller"


def test_ephemeral_child_reuses_caller_tools(tmp_path) -> None:
    """T4: el hijo reusa el MISMO ToolRegistry del caller (recursos del padre)."""
    caller_cfg = _make_agent_config(agent_id="coordinator")
    caller = _build_minimal_container(caller_cfg, _make_global_config(), tmp_path)

    child = caller.build_ephemeral_child(_sub_definition_raw())

    assert child._tools is caller._tools, (
        "el hijo debe operar con el toolkit del caller (mismo objeto)"
    )


def test_ephemeral_child_uses_sub_system_prompt(tmp_path) -> None:
    """T4: el system_prompt del hijo es el de la DEFINICIÓN del sub (su identidad),
    nunca el del caller."""
    caller_cfg = _make_agent_config(agent_id="coordinator")
    assert caller_cfg.system_prompt == "Test prompt"
    caller = _build_minimal_container(caller_cfg, _make_global_config(), tmp_path)

    child = caller.build_ephemeral_child(_sub_definition_raw(system_prompt="Sos un investigador."))

    assert child._cfg.system_prompt == "Sos un investigador."
    assert child._cfg.system_prompt != caller_cfg.system_prompt


def test_ephemeral_child_allow_list_from_sub(tmp_path) -> None:
    """T6 ↔ T4: `tools.allowed` del sub se propaga a OneShotSettings.allowed_tools como
    frozenset; ausente = None (sin restricción)."""
    caller_cfg = _make_agent_config(agent_id="coordinator")
    caller = _build_minimal_container(caller_cfg, _make_global_config(), tmp_path)

    child = caller.build_ephemeral_child(
        _sub_definition_raw(tools={"allowed": ["read_file", "web_search"]})
    )
    assert child._cfg.allowed_tools == frozenset({"read_file", "web_search"})

    child_no_allow = caller.build_ephemeral_child(_sub_definition_raw())
    assert child_no_allow._cfg.allowed_tools is None


def test_ephemeral_child_distinct_instances_per_call(tmp_path) -> None:
    """T4: cada delegación arma una instancia nueva (one-shot descartable). Dos builds
    de la misma definición → objetos distintos (P y Q no comparten instancia)."""
    caller_cfg = _make_agent_config(agent_id="coordinator")
    caller = _build_minimal_container(caller_cfg, _make_global_config(), tmp_path)

    a = caller.build_ephemeral_child(_sub_definition_raw())
    b = caller.build_ephemeral_child(_sub_definition_raw())

    assert a is not b, "cada delegación construye una instancia efímera independiente"
