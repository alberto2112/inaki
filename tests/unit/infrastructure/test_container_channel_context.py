"""
Unit tests para el contexto de canal per-turno (ContextVar).

``AgentContainer.get_channel_context()`` lee el ``ContextVar`` que
``RunAgentUseCase.execute`` publica al inicio de cada turno. No existe más el
slot mutable por-container (``set_channel_context``) que se pisaba entre turnos
concurrentes del mismo agente.

Coverage:
1. get_channel_context devuelve None sin turno en curso
2. get_channel_context refleja el ContextVar publicado (set/reset con token)
3. Aislamiento task-safe: dos tasks concurrentes ven cada una SU contexto
   (regresión de la race con cross-user leak que motivó el refactor)
4. wire_scheduler pasa get_channel_context al SchedulerTool (no lambda: None)
5. La callable del SchedulerTool refleja el contexto del turno en curso
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.tools.scheduler_tool import SchedulerTool
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.domain.value_objects.channel_context import (
    ChannelContext,
    reset_current_channel_context,
    set_current_channel_context,
)
from core.use_cases.run_agent import RunAgentUseCase
from core.domain.value_objects.agent_settings import OneShotSettings
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
    ProviderConfig,
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
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memory=MemoryConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
        delegation=AgentDelegationConfig(enabled=False, allowed_targets=[]),
        providers={"openrouter": ProviderConfig(api_key="test-key")},
    )


def _make_global_config() -> GlobalConfig:
    return GlobalConfig(
        app=AppConfig(ext_dirs=[]),
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memory=MemoryConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
        skills=SkillsConfig(),
        tools=ToolsConfig(),
        scheduler=SchedulerConfig(),
        workspace=WorkspaceConfig(),
        delegation=DelegationConfig(),
        providers={"openrouter": ProviderConfig(api_key="test-key")},
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
        settings=OneShotSettings(
            agent_id=agent_config.id,
            system_prompt=agent_config.system_prompt,
            circuit_breaker_threshold=agent_config.tools.circuit_breaker_threshold,
        ),
    )
    return container


def _make_mock_schedule_uc() -> MagicMock:
    return MagicMock(spec=ScheduleTaskUseCase)


# ---------------------------------------------------------------------------
# Test 1 — get_channel_context devuelve None sin turno en curso
# ---------------------------------------------------------------------------


def test_get_channel_context_devuelve_none_sin_turno() -> None:
    """
    Sin un execute() en curso (nadie publicó el ContextVar), get_channel_context()
    debe devolver None.
    """
    agent_cfg = _make_agent_config("agente-a")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    assert container.get_channel_context() is None, (
        "get_channel_context() debe devolver None sin turno en curso"
    )


# ---------------------------------------------------------------------------
# Test 2 — get_channel_context refleja el ContextVar publicado
# ---------------------------------------------------------------------------


def test_get_channel_context_refleja_contextvar() -> None:
    """
    Publicar un contexto en el ContextVar (lo que hace execute() al iniciar el
    turno) debe ser visible vía get_channel_context(); el reset lo restaura.
    """
    agent_cfg = _make_agent_config("agente-b")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    ctx = ChannelContext(channel_type="telegram", user_id="789")
    token = set_current_channel_context(ctx)
    try:
        resultado = container.get_channel_context()
        assert resultado is ctx, "get_channel_context() debe devolver el contexto del turno"
        assert resultado.routing_key == "telegram:789"
    finally:
        reset_current_channel_context(token)

    assert container.get_channel_context() is None, (
        "Tras el reset (fin del turno), get_channel_context() debe devolver None"
    )


# ---------------------------------------------------------------------------
# Test 3 — Aislamiento task-safe entre turnos concurrentes
# ---------------------------------------------------------------------------


async def test_turnos_concurrentes_ven_cada_uno_su_contexto() -> None:
    """
    Regresión de la race con cross-user leak: dos turnos concurrentes del MISMO
    agente (ej: REST admin + Telegram) publicaban su contexto en un slot mutable
    compartido y se pisaban — un turno podía resolver {{CHANNEL.*}} o enrutar
    una tool con la identidad del otro. Con el ContextVar, cada task de asyncio
    ve SU propio contexto aunque se intercalen en los awaits.
    """
    agent_cfg = _make_agent_config("agente-race")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    barrera = asyncio.Barrier(2)
    leidos: dict[str, ChannelContext | None] = {}

    async def turno(nombre: str, ctx: ChannelContext) -> None:
        token = set_current_channel_context(ctx)
        try:
            # Sincronizar para garantizar que ambos turnos están in-flight a la
            # vez ANTES de leer — con el slot mutable viejo, el segundo set
            # pisaba al primero y este test fallaba.
            await barrera.wait()
            leidos[nombre] = container.get_channel_context()
        finally:
            reset_current_channel_context(token)

    ctx_juan = ChannelContext(channel_type="telegram", user_id="juan-id", username="juan")
    ctx_rest = ChannelContext(channel_type="cli", user_id="session-x")

    async with asyncio.TaskGroup() as tg:
        tg.create_task(turno("telegram", ctx_juan))
        tg.create_task(turno("rest", ctx_rest))

    assert leidos["telegram"] is ctx_juan, "El turno de Telegram debe ver SU contexto"
    assert leidos["rest"] is ctx_rest, "El turno REST debe ver SU contexto"


# ---------------------------------------------------------------------------
# Test 4 — wire_scheduler pasa get_channel_context al SchedulerTool
# ---------------------------------------------------------------------------


def test_wire_scheduler_pasa_get_channel_context() -> None:
    """
    wire_scheduler debe pasar self.get_channel_context al SchedulerTool,
    NO un lambda: None. La callable debe devolver None sin turno en curso
    y reflejar el contexto del turno cuando hay uno publicado.
    """
    agent_cfg = _make_agent_config("agente-f")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)
    uc = _make_mock_schedule_uc()

    container.wire_scheduler(uc, "America/Argentina/Buenos_Aires")

    assert "scheduler" in container._tools._tools
    tool = container._tools._tools["scheduler"]
    assert isinstance(tool, SchedulerTool)

    # La callable debe devolver None sin turno en curso
    assert tool._get_channel_context() is None, (
        "La callable del SchedulerTool debe devolver None cuando no hay turno"
    )

    # Con un turno publicado, la callable debe reflejarlo
    ctx = ChannelContext(channel_type="telegram", user_id="42")
    token = set_current_channel_context(ctx)
    try:
        resultado = tool._get_channel_context()
        assert resultado is ctx, (
            "La callable del SchedulerTool debe reflejar el contexto del turno en curso"
        )
    finally:
        reset_current_channel_context(token)


# ---------------------------------------------------------------------------
# Test 5 — La callable del SchedulerTool no es lambda: None (era el bug anterior)
# ---------------------------------------------------------------------------


def test_wire_scheduler_callable_no_es_lambda_none() -> None:
    """
    Antes de T6, wire_scheduler pasaba lambda: None hardcodeado.
    Este test verifica que la callable ya NO es un lambda fijo —
    debe reflejar el contexto real del turno en curso.
    """
    agent_cfg = _make_agent_config("agente-g")
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)
    uc = _make_mock_schedule_uc()

    container.wire_scheduler(uc, "UTC")

    tool = container._tools._tools["scheduler"]
    assert isinstance(tool, SchedulerTool)

    # Si publicamos un contexto y la callable lo refleja, NO es lambda: None
    ctx = ChannelContext(channel_type="cli", user_id="local")
    token = set_current_channel_context(ctx)
    try:
        resultado = tool._get_channel_context()
        assert resultado is not None, (
            "La callable del SchedulerTool NO debe ser lambda: None — "
            "debe reflejar el contexto real del turno"
        )
        assert resultado.channel_type == "cli"
    finally:
        reset_current_channel_context(token)
