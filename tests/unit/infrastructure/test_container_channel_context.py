"""
Unit tests para T6 — AgentContainer channel context holder.

Coverage:
1. _channel_context inicializa en None
2. get_channel_context devuelve None inicialmente
3. set_channel_context almacena el contexto
4. get_channel_context devuelve el contexto seteado
5. set_channel_context con None limpia el contexto
6. wire_scheduler pasa get_channel_context al SchedulerTool (no lambda: None)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.tools.scheduler_tool import SchedulerTool
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.domain.value_objects.channel_context import ChannelContext
from core.use_cases.run_agent import RunAgentUseCase
from core.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from core.use_cases.schedule_task import ScheduleTaskUseCase
from infrastructure.config import (
    AgentConfig,
    AgentDelegationConfig,
    AppConfig,
    ChatHistoryConfig,
    DelegationConfig,
    EmbeddingConfig,
    GlobalConfig,
    LLMConfig,
    MemoryConfig,
    SchedulerConfig,
    SkillsConfig,
    ToolsConfig,
    WorkspaceConfig,
)
from infrastructure.container import AgentContainer


# ---------------------------------------------------------------------------
# Helpers — mismos patrones que test_container_wire_scheduler.py
# ---------------------------------------------------------------------------


class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _make_agent_config(agent_id: str = "test-agent") -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name=agent_id.capitalize(),
        description=f"Agente {agent_id}",
        system_prompt="Prompt de prueba",
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memory=MemoryConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
        delegation=AgentDelegationConfig(enabled=False, allowed_targets=[]),
    )


def _make_global_config() -> GlobalConfig:
    return GlobalConfig(
        app=AppConfig(ext_dirs=[]),
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memory=MemoryConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
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
    Construye un AgentContainer sin IO real.
    Usa __new__ + inyección manual de atributos.
    """
    container = AgentContainer.__new__(AgentContainer)
    container.agent_config = agent_config
    container._global_config = global_config
    container._delegation_wired = False
    container._scheduler_wired = False
    container._channel_context = None
    container._llm = AsyncMock()
    container._embedder = FakeEmbedder()
    container._tools = ToolRegistry(embedder=container._embedder)
    dummy_tool = MagicMock()
    dummy_tool.name = "dummy_tool"
    dummy_tool.description = "Herramienta dummy"
    dummy_tool.parameters_schema = {"type": "object", "properties": {}}
    container._tools.register(dummy_tool)
    container.run_agent = MagicMock(spec=RunAgentUseCase)
    container.run_agent._extra_system_sections = []
    container.run_agent_one_shot = RunAgentOneShotUseCase(
        llm=container._llm,
        tools=container._tools,
        agent_config=agent_config,
    )
    return container


def _make_mock_schedule_uc() -> MagicMock:
    return MagicMock(spec=ScheduleTaskUseCase)


# ---------------------------------------------------------------------------
# Test 1 — _channel_context inicializa en None
# ---------------------------------------------------------------------------


def test_channel_context_inicializa_en_none() -> None:
    """
    _channel_context debe ser None al construir AgentContainer.
    """
    agent_cfg = _make_agent_config("agente-a")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    assert container._channel_context is None, (
        "_channel_context debe inicializar en None"
    )


# ---------------------------------------------------------------------------
# Test 2 — get_channel_context devuelve None inicialmente
# ---------------------------------------------------------------------------


def test_get_channel_context_devuelve_none_inicialmente() -> None:
    """
    get_channel_context() debe devolver None antes de cualquier set.
    """
    agent_cfg = _make_agent_config("agente-b")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    resultado = container.get_channel_context()

    assert resultado is None, (
        "get_channel_context() debe devolver None sin haber seteado contexto"
    )


# ---------------------------------------------------------------------------
# Test 3 — set_channel_context almacena el contexto
# ---------------------------------------------------------------------------


def test_set_channel_context_almacena_contexto() -> None:
    """
    set_channel_context(ctx) debe almacenar ctx en _channel_context.
    """
    agent_cfg = _make_agent_config("agente-c")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    ctx = ChannelContext(channel_type="telegram", user_id="123456")
    container.set_channel_context(ctx)

    assert container._channel_context is ctx, (
        "_channel_context debe referenciar el mismo objeto pasado a set_channel_context"
    )


# ---------------------------------------------------------------------------
# Test 4 — get_channel_context devuelve el contexto seteado
# ---------------------------------------------------------------------------


def test_get_channel_context_devuelve_contexto_seteado() -> None:
    """
    get_channel_context() debe devolver el ChannelContext pasado a set_channel_context.
    """
    agent_cfg = _make_agent_config("agente-d")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    ctx = ChannelContext(channel_type="telegram", user_id="789")
    container.set_channel_context(ctx)

    resultado = container.get_channel_context()

    assert resultado is ctx, (
        "get_channel_context() debe devolver el mismo objeto seteado"
    )
    assert resultado.channel_type == "telegram"
    assert resultado.user_id == "789"
    assert resultado.routing_key == "telegram:789"


# ---------------------------------------------------------------------------
# Test 5 — set_channel_context con None limpia el contexto
# ---------------------------------------------------------------------------


def test_set_channel_context_none_limpia_contexto() -> None:
    """
    set_channel_context(None) debe limpiar el contexto previo.
    get_channel_context() debe devolver None después.
    """
    agent_cfg = _make_agent_config("agente-e")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    ctx = ChannelContext(channel_type="telegram", user_id="999")
    container.set_channel_context(ctx)
    assert container.get_channel_context() is ctx  # precondición

    container.set_channel_context(None)

    assert container.get_channel_context() is None, (
        "Luego de set_channel_context(None), get_channel_context() debe devolver None"
    )


# ---------------------------------------------------------------------------
# Test 6 — wire_scheduler pasa get_channel_context al SchedulerTool
# ---------------------------------------------------------------------------


def test_wire_scheduler_pasa_get_channel_context() -> None:
    """
    wire_scheduler debe pasar self.get_channel_context al SchedulerTool,
    NO un lambda: None. Verificamos que la callable almacenada en
    SchedulerTool._get_channel_context es el método get_channel_context
    del container y devuelve None inicialmente pero refleja cambios posteriores.
    """
    agent_cfg = _make_agent_config("agente-f")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)
    uc = _make_mock_schedule_uc()

    container.wire_scheduler(uc, "America/Argentina/Buenos_Aires")

    assert "scheduler" in container._tools._tools
    tool = container._tools._tools["scheduler"]
    assert isinstance(tool, SchedulerTool)

    # La callable debe devolver None inicialmente (sin contexto seteado)
    assert tool._get_channel_context() is None, (
        "La callable del SchedulerTool debe devolver None cuando no hay contexto"
    )

    # Al setear contexto en el container, la callable debe reflejarlo
    ctx = ChannelContext(channel_type="telegram", user_id="42")
    container.set_channel_context(ctx)

    resultado = tool._get_channel_context()
    assert resultado is ctx, (
        "La callable del SchedulerTool debe reflejar el contexto seteado en el container"
    )


# ---------------------------------------------------------------------------
# Test 7 — La callable del SchedulerTool no es lambda: None (era el bug anterior)
# ---------------------------------------------------------------------------


def test_wire_scheduler_callable_no_es_lambda_none() -> None:
    """
    Antes de T6, wire_scheduler pasaba lambda: None hardcodeado.
    Este test verifica que la callable ya NO es un lambda fijo —
    debe ser la misma instancia de método que container.get_channel_context.
    """
    agent_cfg = _make_agent_config("agente-g")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)
    uc = _make_mock_schedule_uc()

    container.wire_scheduler(uc, "UTC")

    tool: SchedulerTool = container._tools._tools["scheduler"]

    # Si seteamos un contexto y la callable lo refleja, NO es lambda: None
    ctx = ChannelContext(channel_type="cli", user_id="local")
    container.set_channel_context(ctx)

    resultado = tool._get_channel_context()
    assert resultado is not None, (
        "La callable del SchedulerTool NO debe ser lambda: None — "
        "debe reflejar el contexto real del container"
    )
    assert resultado.channel_type == "cli"
