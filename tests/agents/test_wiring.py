"""``inaki.agents.wiring``: el hijo efímero y la sección de descubrimiento, puros."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from inaki.agents.wiring import build_discovery_section, build_ephemeral_child
from inaki.kernel.ports.outbound.turn_tracer_port import NullTurnTracer
from inaki.kernel.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from tests.app.conftest import agent_cfg


def _sub(**overrides: object) -> dict:
    raw: dict = {"id": "researcher", "name": "Researcher", "description": "Investiga."}
    raw.update(overrides)
    return raw


def _hijo(raw: dict, *, llm: AsyncMock | None = None, tools: object | None = None):
    return build_ephemeral_child(
        raw,
        caller_cfg=agent_cfg("coordinator"),
        caller_llm=llm or AsyncMock(),
        tools=tools or MagicMock(),  # type: ignore[arg-type]
        tracer=NullTurnTracer(),
        thinking_indicator=False,
    )


def test_hereda_la_instancia_de_llm_del_caller_sin_override() -> None:
    llm = AsyncMock()
    child = _hijo(_sub(), llm=llm)
    assert isinstance(child, RunAgentOneShotUseCase)
    assert child._llm is llm


def test_con_override_de_llm_construye_una_instancia_nueva_con_los_providers_del_caller() -> None:
    sentinel = AsyncMock()
    with patch("inaki.agents.wiring.LLMProviderFactory.create", return_value=sentinel) as create:
        child = _hijo(_sub(llm={"model": "child-model"}))
    assert child._llm is sentinel
    llm_cfg, providers = create.call_args[0]
    assert llm_cfg.model == "child-model" and llm_cfg.provider == "openrouter"
    assert "openrouter" in providers, "el hijo hereda el registry providers del caller"


def test_reusa_las_tools_del_caller_y_usa_el_prompt_del_sub() -> None:
    tools = MagicMock()
    child = _hijo(_sub(system_prompt="Sos un investigador."), tools=tools)
    assert child._tools is tools
    assert child._cfg.system_prompt == "Sos un investigador."


def test_la_allow_list_del_sub_viaja_como_frozenset_y_ausente_es_none() -> None:
    con = _hijo(_sub(tools={"allowed": ["read_file", "web_search"]}))
    sin = _hijo(_sub())
    assert con._cfg.allowed_tools == frozenset({"read_file", "web_search"})
    assert sin._cfg.allowed_tools is None


def test_cada_llamada_construye_una_instancia_distinta() -> None:
    assert _hijo(_sub()) is not _hijo(_sub())


# --- descubrimiento ----------------------------------------------------------


def test_el_delta_crudo_manda_sobre_el_agente_construido() -> None:
    target = MagicMock(name="B", description="Container-side.", tool_names=["container_tool"])
    target.name = "B"
    raw = {"id": "b", "name": "Agent B", "description": "Raw-side description."}

    seccion = build_discovery_section(
        "parent", ["b"], get_sub_agent_raw=lambda _: raw, describir_agente=lambda _: target
    )

    assert "Raw-side description." in seccion
    assert "container_tool" not in seccion
    assert "inherits this agent's full toolkit" in seccion


def test_sin_targets_o_todos_desconocidos_no_hay_seccion() -> None:
    assert (
        build_discovery_section("p", [], get_sub_agent_raw=None, describir_agente=lambda _: None)
        == ""
    )
    assert (
        build_discovery_section(
            "p", ["ghost"], get_sub_agent_raw=lambda _: None, describir_agente=lambda _: None
        )
        == ""
    )
