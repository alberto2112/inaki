"""Tests — ``execute(skip_routing=True)``: un turno cuyo input NO es intención del usuario.

Caso real (bg-1001): el resultado de una delegación (60.388 chars) se entregaba al
padre como un turno completo; el routing embebía el texto ENTERO contra OpenAI y el
400 por exceso de contexto mataba la entrega. Un ``[bg-N]`` es un RESULTADO, no una
consulta: embeberlo selecciona tools por el tema del resultado, no por la necesidad.

``skip_routing`` toma el mismo camino que el bypass por input corto (selección sticky
previa + ``tools.pinned``), sin embedding ni pre-fetch de knowledge.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from inaki.kernel._turn_pipeline import run_semantic_routing
from inaki.kernel.domain.agent_settings import RunAgentSettings
from inaki.kernel.domain.conversation_state import ConversationState
from inaki.kernel.domain.llm_response import LLMResponse
from inaki.kernel.domain.skill import Skill
from inaki.kernel.run_agent import RunAgentUseCase


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": f"{name} desc"}}


def _names(schemas: list[dict]) -> list[str]:
    return [sch["function"]["name"] for sch in schemas]


def _settings(**overrides: Any) -> RunAgentSettings:
    base: dict[str, Any] = dict(
        agent_id="test",
        tools_min_tools=1,  # routing de tools activo
        skills_min_skills=0,  # routing de skills activo
        tools_pinned=frozenset({"delegate"}),
        min_words_threshold=0,  # el bypass por input corto NO interviene
    )
    base.update(overrides)
    return RunAgentSettings(**base)


def _ports(all_tools: list[str]):
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(side_effect=AssertionError("no debe embeber"))
    skills = AsyncMock()
    skills.list_all = AsyncMock(
        return_value=[
            Skill(id="agenda", name="agenda", description="d"),
            Skill(id="poema", name="poema", description="d"),
        ]
    )
    skills.retrieve = AsyncMock(side_effect=AssertionError("no debe recuperar skills"))
    tools = MagicMock()
    tools.get_schemas = MagicMock(return_value=[_schema(n) for n in all_tools])
    tools.get_schemas_relevant = AsyncMock(side_effect=AssertionError("no debe rutear tools"))
    return embedder, skills, tools


async def _routing(prev_state: ConversationState, *, skip_routing: bool = True):
    embedder, skills, tools = _ports(["list_events", "read_file", "web_search", "delegate"])
    outcome = await run_semantic_routing(
        query="[bg-1] success: " + "x" * 5000,
        tools_override=None,
        prev_state=prev_state,
        settings=_settings(),
        embedder=embedder,
        skills=skills,
        tools=tools,
        skip_routing=skip_routing,
    )
    return outcome, embedder


async def test_skip_routing_no_embebe_y_hereda_sticky_mas_pinned():
    prev = ConversationState(sticky_skills={"agenda": 2}, sticky_tools={"list_events": 2})

    outcome, embedder = await _routing(prev)

    embedder.embed_query.assert_not_called()
    assert outcome.routing_bypass is True
    assert outcome.query_vec is None
    assert _names(outcome.tool_schemas) == ["list_events", "delegate"]
    assert [s.id for s in outcome.retrieved_skills] == ["agenda"]


async def test_skip_routing_no_consume_ttl_ni_marca_estado_sucio():
    """Igual que el bypass por input corto: no se observó ninguna intención, no hay
    nada con qué decaer los TTL; el sticky queda intacto y no se persiste."""
    prev = ConversationState(sticky_skills={"agenda": 2}, sticky_tools={"list_events": 1})

    outcome, _ = await _routing(prev)

    assert outcome.state_dirty is False
    assert outcome.new_sticky_skills == {"agenda": 2}
    assert outcome.new_sticky_tools == {"list_events": 1}


async def test_skip_routing_sin_sticky_expone_solo_pinned_no_todo_el_catalogo():
    """Sin sticky previo no se cae al catálogo entero (~25 schemas): solo lo pinneado.
    Lo demás se alcanza por page-in."""
    outcome, embedder = await _routing(ConversationState())

    embedder.embed_query.assert_not_called()
    assert _names(outcome.tool_schemas) == ["delegate"]
    assert outcome.retrieved_skills == []


async def test_sin_skip_routing_el_routing_corre_normal():
    embedder, skills, tools = _ports(["list_events", "delegate"])
    embedder.embed_query = AsyncMock(return_value=[0.1] * 384)
    skills.retrieve = AsyncMock(return_value=[])
    tools.get_schemas_relevant = AsyncMock(return_value=[_schema("list_events")])

    await run_semantic_routing(
        query="consulta real",
        tools_override=None,
        prev_state=ConversationState(),
        settings=_settings(),
        embedder=embedder,
        skills=skills,
        tools=tools,
    )

    embedder.embed_query.assert_awaited_once()


async def test_execute_skip_routing_no_embebe_ni_consulta_knowledge(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """End-to-end del use case: ni embedding ni pre-fetch aunque haya orquestrador."""
    mock_skills.list_all.return_value = []
    mock_tools.get_schemas.return_value = [
        _schema("list_events"),
        _schema("read_file"),
        _schema("delegate"),
    ]
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState(sticky_tools={"list_events": 2})
    knowledge = MagicMock()
    knowledge.pre_fetch_enabled = True
    knowledge.retrieve_all = AsyncMock(side_effect=AssertionError("no debe consultar knowledge"))
    uc = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        settings=_settings(skills_min_skills=100),
        knowledge_orchestrator=knowledge,
    )

    respuesta = await uc.execute("[bg-1] success: " + "x" * 60000, skip_routing=True)

    assert respuesta == "ok"
    mock_embedder.embed_query.assert_not_called()
    knowledge.retrieve_all.assert_not_called()
    visibles = _names(mock_llm.complete.call_args.kwargs["tools"])
    assert set(visibles) == {"list_events", "delegate"}
    mock_history.save_state.assert_not_called()
