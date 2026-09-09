"""Las pasadas de cruce del ensamblador sobre un borrador, sin ensamblar el proceso.

``_Borrador`` es privado del ensamblador; estos tests lo construyen con fakes
para probar cada ``_wire_*`` aislada: qué registra, con qué config, y qué
anota en el borrador para el runtime.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from inaki.agents.delegation.delegate_tool import DelegateTool
from inaki.app.assembly import (
    _Borrador,
    _Registros,
    _wire_delegation,
    _wire_scheduler,
    _wire_telegram_tools,
    contexto_del_turno,
)
from inaki.channels.telegram.files.downloader import TelegramFileDownloader
from inaki.config import AgentConfig
from inaki.kernel.ports.outbound.turn_tracer_port import NullTurnTracer
from inaki.kernel.use_cases.conversation_history import ConversationHistory
from inaki.kernel.use_cases.run_agent import RunAgentUseCase
from inaki.kernel.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from inaki.memory.wiring import MemoryJobs
from inaki.scheduler.tools.scheduler_tool import SchedulerTool
from inaki.shared.channel_context import (
    ChannelContext,
    reset_current_channel_context,
    set_current_channel_context,
)
from inaki.tools.registry import ToolRegistry
from tests.app.conftest import FakeEmbedder, RegistryFalso, agent_cfg, global_cfg


def borrador(cfg: AgentConfig, *, tools: tuple[str, ...] = ("dummy_tool",)) -> _Borrador:
    """Un agente en construcción con fakes: LLM, run_agent mockeado, tools dummy."""
    embedder = FakeEmbedder()
    llm = AsyncMock()
    registry = ToolRegistry(embedder=embedder)
    for name in tools:
        t = MagicMock()
        t.name = name
        t.description = f"Tool {name}"
        t.parameters_schema = {"type": "object", "properties": {}}
        registry.register(t)
    run_agent = MagicMock(spec=RunAgentUseCase)
    one_shot = RunAgentOneShotUseCase(
        llm=llm,
        tools=registry,
        settings=MagicMock(agent_id=cfg.id, system_prompt=cfg.system_prompt),
    )
    return _Borrador(
        cfg=cfg,
        llm=llm,
        embedder=embedder,
        memory=AsyncMock(),
        history=AsyncMock(),
        skills=AsyncMock(),
        tools=registry,
        knowledge=MagicMock(),
        transcribe_audio=None,
        run_agent=run_agent,
        run_agent_one_shot=one_shot,
        conversation=ConversationHistory(AsyncMock(), cfg.id),
        jobs=MemoryJobs(None, None),
        scope_registry=MagicMock(),
        tracer=NullTurnTracer(),
    )


def _harness(**overrides: object) -> Any:
    base: dict[str, object] = {
        "background_queue": MagicMock(),
        "scheduler": SimpleNamespace(use_case=MagicMock(), service=MagicMock()),
        "photos": None,
        "telegram_file_repo": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _seccion(b: _Borrador) -> str | None:
    llamada = cast(MagicMock, b.run_agent).set_extra_system_sections
    return llamada.call_args[0][0][0] if llamada.called else None


# --- delegación ------------------------------------------------------------


def test_delegacion_deshabilitada_no_registra_delegate_ni_seccion() -> None:
    b = borrador(agent_cfg("worker"))
    registry: Any = RegistryFalso([b.cfg], [agent_cfg("sub")])

    _wire_delegation(b, global_cfg(), registry, {"worker": b}, _harness())

    assert "delegate" not in b.tools._tools
    assert _seccion(b) is None
    assert isinstance(b.run_agent_one_shot, RunAgentOneShotUseCase)


def test_delegacion_habilitada_registra_delegate_con_la_config_global() -> None:
    b = borrador(agent_cfg("coordinator", delegation_enabled=True, allowed_targets=["specialist"]))
    registry: Any = RegistryFalso([b.cfg], [agent_cfg("specialist")])
    harness = _harness()

    _wire_delegation(
        b, global_cfg(max_iterations_per_sub=7, timeout_seconds=30), registry, {}, harness
    )

    tool = cast(DelegateTool, b.tools._tools["delegate"])
    assert isinstance(tool, DelegateTool)
    assert tool._allowed_targets == ["specialist"]
    assert tool._max_iterations_per_sub == 7 and tool._timeout_seconds == 30
    cast(MagicMock, b.run_agent).set_background_queue.assert_called_once_with(
        harness.background_queue
    )


def test_la_allow_list_filtra_los_sub_agentes_y_vacia_permite_todos() -> None:
    subs = [agent_cfg("b"), agent_cfg("c")]
    filtrado = borrador(agent_cfg("p", delegation_enabled=True, allowed_targets=["b"]))
    abierto = borrador(agent_cfg("q", delegation_enabled=True))
    registry: Any = RegistryFalso([filtrado.cfg, abierto.cfg], subs)

    _wire_delegation(filtrado, global_cfg(), registry, {}, _harness())
    _wire_delegation(abierto, global_cfg(), registry, {}, _harness())

    assert cast(DelegateTool, filtrado.tools._tools["delegate"])._allowed_targets == ["b"]
    assert cast(DelegateTool, abierto.tools._tools["delegate"])._allowed_targets == ["b", "c"]


def test_sin_sub_agentes_elegibles_no_hay_delegate() -> None:
    b = borrador(agent_cfg("p", delegation_enabled=True, allowed_targets=["ghost"]))
    registry: Any = RegistryFalso([b.cfg], [agent_cfg("b")])

    _wire_delegation(b, global_cfg(), registry, {}, _harness())

    assert "delegate" not in b.tools._tools


def test_build_child_resuelve_contra_el_caller_y_desconocido_da_none() -> None:
    b = borrador(agent_cfg("late-a", delegation_enabled=True))
    registry: Any = RegistryFalso([b.cfg], [agent_cfg("late-b")])

    _wire_delegation(b, global_cfg(), registry, {}, _harness())

    tool = cast(DelegateTool, b.tools._tools["delegate"])
    child = tool._build_child("late-b")
    assert isinstance(child, RunAgentOneShotUseCase)
    assert child._llm is b.llm, "el hijo efímero hereda el LLM del caller"
    assert child._tools is b.tools
    assert tool._build_child("ghost") is None


def test_la_seccion_de_descubrimiento_sale_del_delta_crudo_del_registry() -> None:
    b = borrador(agent_cfg("parent", delegation_enabled=True))
    raw = {
        "id": "deep_research",
        "name": "Deep Research Agent",
        "description": "Autonomous sub-agent\nfor in-depth research.",
        "tools": {"allowed": ["web_search", "write_file"]},
    }
    registry: Any = RegistryFalso([b.cfg], [agent_cfg("deep_research")], raw={"deep_research": raw})

    _wire_delegation(b, global_cfg(), registry, {}, _harness())

    seccion = _seccion(b)
    assert seccion is not None
    assert "Deep Research Agent" in seccion
    assert "Autonomous sub-agent for in-depth research." in seccion
    assert "web_search, write_file (subset of this agent's toolkit)" in seccion


def test_sin_delta_crudo_la_seccion_cae_al_agente_construido_y_saltea_desconocidos() -> None:
    b = borrador(agent_cfg("parent", delegation_enabled=True))
    target = borrador(
        agent_cfg("agent-b").model_copy(update={"description": "Specialist B."}),
        tools=("web_search", "fetch_url"),
    )
    registry: Any = RegistryFalso([b.cfg], [agent_cfg("agent-b"), agent_cfg("ghost")], raw={})

    _wire_delegation(b, global_cfg(), registry, {"agent-b": target}, _harness())

    seccion = _seccion(b)
    assert seccion is not None
    assert "agent-b" in seccion and "Specialist B." in seccion
    assert "web_search" in seccion and "fetch_url" in seccion
    assert "ghost" not in seccion


def test_todos_los_targets_desconocidos_no_deja_seccion() -> None:
    b = borrador(agent_cfg("parent", delegation_enabled=True))
    registry: Any = RegistryFalso([b.cfg], [agent_cfg("ghost")], raw={})

    _wire_delegation(b, global_cfg(), registry, {}, _harness())

    assert "delegate" in b.tools._tools
    assert _seccion(b) is None


# --- scheduler y contexto del turno ------------------------------------------


def test_wire_scheduler_registra_la_tool_con_la_config_del_agente() -> None:
    b = borrador(agent_cfg("my-agent"))
    harness = _harness()
    cfg = global_cfg()
    cfg.user.timezone = "Europe/Madrid"

    _wire_scheduler(b, cfg, harness)

    tool = cast(SchedulerTool, b.tools._tools["scheduler"])
    assert isinstance(tool, SchedulerTool)
    assert tool._agent_id == "my-agent" and tool._user_timezone == "Europe/Madrid"
    assert tool._uc is harness.scheduler.use_case and tool._runner is harness.scheduler.service
    assert b.scheduler == (harness.scheduler, "Europe/Madrid")


def test_la_tool_del_scheduler_lee_el_contexto_del_turno_en_curso() -> None:
    b = borrador(agent_cfg("a"))
    _wire_scheduler(b, global_cfg(), _harness())
    leer = cast(SchedulerTool, b.tools._tools["scheduler"])._get_channel_context

    assert leer() is None
    ctx = ChannelContext(channel_type="telegram", user_id="42")
    token = set_current_channel_context(ctx)
    try:
        assert leer() is ctx
    finally:
        reset_current_channel_context(token)
    assert leer() is None


async def test_dos_turnos_concurrentes_ven_cada_uno_su_contexto() -> None:
    """Regresión de la race con cross-user leak: el contexto es un ContextVar por task."""
    vistos: dict[str, ChannelContext | None] = {}

    async def turno(user_id: str) -> None:
        token = set_current_channel_context(
            ChannelContext(channel_type="telegram", user_id=user_id)
        )
        try:
            await asyncio.sleep(0)
            vistos[user_id] = contexto_del_turno.get_channel_context()
        finally:
            reset_current_channel_context(token)

    await asyncio.gather(turno("1"), turno("2"))

    assert vistos["1"] is not None and vistos["1"].user_id == "1"
    assert vistos["2"] is not None and vistos["2"].user_id == "2"


# --- telegram ----------------------------------------------------------------


def test_sin_token_no_hay_outbound_ni_tools_de_telegram() -> None:
    b = borrador(agent_cfg("a"))

    _wire_telegram_tools(b, global_cfg(), _harness(), _Registros())

    assert b.outbounds.list_channels() == []
    assert not any(n.startswith(("send_to_telegram", "download_from")) for n in b.tools._tools)


def test_con_token_hay_outbound_y_las_tres_tools_resuelven_el_bot_tarde() -> None:
    b = borrador(agent_cfg("a", channels={"telegram": {"token": "T"}}))
    registros = _Registros()

    _wire_telegram_tools(b, global_cfg(), _harness(telegram_file_repo=MagicMock()), registros)

    assert b.outbounds.list_channels() == ["telegram"]
    assert {"send_to_telegram", "send_telegram_message", "download_from_telegram"} <= set(
        b.tools._tools
    )
    assert b.telegram_file_downloader is not None
    bot = MagicMock()
    registros.bots["a"] = bot
    assert cast(TelegramFileDownloader, b.telegram_file_downloader)._get_telegram_bot() is bot
