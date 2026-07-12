"""Tests unitarios — tools pinneadas en el semantic routing (feature ``tools.pinned``).

Una tool pinneada está SIEMPRE visible para el LLM: su schema se uniona al
resultado del routing sin contar contra ``top_k`` ni consumir TTL sticky.
Motivación (caso real): el LLM decidió delegar a mitad de un turno pero
``delegate`` no estaba en el set visible — el routing por embedding solo la
habría traído si las palabras del USUARIO la matcheaban.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from core.domain.value_objects.agent_settings import RunAgentSettings
from core.domain.value_objects.conversation_state import ConversationState
from core.use_cases._turn_pipeline import _union_pinned_schemas, run_semantic_routing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": f"{name} desc"}}


def _make_settings(**overrides: Any) -> RunAgentSettings:
    base: dict[str, Any] = dict(
        agent_id="test",
        tools_min_tools=3,  # bajo para activar routing con pocos schemas
        tools_top_k=2,
        skills_min_skills=100,  # routing de skills inactivo en estos tests
        tools_pinned=frozenset({"delegate"}),
    )
    base.update(overrides)
    return RunAgentSettings(**base)


def _make_ports(all_tools: list[str], routed: list[str]):
    """Mocks mínimos de (embedder, skills, tools) para run_semantic_routing."""
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 384)
    skills = AsyncMock()
    skills.list_all = AsyncMock(return_value=[])
    tools = MagicMock()
    tools.get_schemas = MagicMock(return_value=[_schema(n) for n in all_tools])
    tools.get_schemas_relevant = AsyncMock(return_value=[_schema(n) for n in routed])
    return embedder, skills, tools


async def _run(
    settings,
    embedder,
    skills,
    tools,
    *,
    query="investigá el tema a fondo",
    prev_state=None,
    tools_override=None,
):
    return await run_semantic_routing(
        query=query,
        tools_override=tools_override,
        prev_state=prev_state or ConversationState(),
        settings=settings,
        embedder=embedder,
        skills=skills,
        tools=tools,
    )


def _names(schemas: list[dict]) -> list[str]:
    return [sch["function"]["name"] for sch in schemas]


# ---------------------------------------------------------------------------
# _union_pinned_schemas (función pura)
# ---------------------------------------------------------------------------


def test_union_agrega_pinned_faltante_preservando_orden():
    selected = [_schema("tool_a"), _schema("tool_b")]
    catalog = [_schema("tool_a"), _schema("tool_b"), _schema("delegate")]

    result = _union_pinned_schemas(selected, catalog, frozenset({"delegate"}))

    assert _names(result) == ["tool_a", "tool_b", "delegate"]


def test_union_no_duplica_pinned_ya_seleccionada():
    selected = [_schema("delegate"), _schema("tool_a")]
    catalog = [_schema("delegate"), _schema("tool_a")]

    result = _union_pinned_schemas(selected, catalog, frozenset({"delegate"}))

    assert _names(result) == ["delegate", "tool_a"]


def test_union_ignora_pinned_inexistente_en_registry():
    """Típico: un agente sin `delegate` registrada (o typo en config)."""
    selected = [_schema("tool_a")]
    catalog = [_schema("tool_a")]

    result = _union_pinned_schemas(selected, catalog, frozenset({"delegate", "fantasma"}))

    assert _names(result) == ["tool_a"]


# ---------------------------------------------------------------------------
# run_semantic_routing — integración de la unión en los tres caminos
# ---------------------------------------------------------------------------


async def test_routing_activo_suma_delegate_al_top_k():
    """El routing eligió otras tools por embedding; delegate entra igual."""
    settings = _make_settings()
    embedder, skills, tools = _make_ports(
        all_tools=["tool_a", "tool_b", "tool_c", "delegate"],
        routed=["tool_a", "tool_b"],
    )

    outcome = await _run(settings, embedder, skills, tools)

    assert set(_names(outcome.tool_schemas)) == {"tool_a", "tool_b", "delegate"}


async def test_pinned_no_entra_al_sticky_state():
    """La visibilidad pinneada es per-turno, no consume TTL ni persiste estado."""
    settings = _make_settings()
    embedder, skills, tools = _make_ports(
        all_tools=["tool_a", "tool_b", "tool_c", "delegate"],
        routed=["tool_a", "tool_b"],
    )

    outcome = await _run(settings, embedder, skills, tools)

    assert "delegate" not in outcome.new_sticky_tools
    assert set(outcome.new_sticky_tools) == {"tool_a", "tool_b"}


async def test_bypass_por_input_corto_tambien_suma_pinned():
    """El camino de short-input hereda sticky Y uniona las pinneadas."""
    settings = _make_settings(min_words_threshold=5)
    embedder, skills, tools = _make_ports(
        all_tools=["tool_a", "tool_b", "tool_c", "delegate"],
        routed=[],
    )
    prev = ConversationState(sticky_tools={"tool_c": 2})

    outcome = await _run(settings, embedder, skills, tools, query="ok dale", prev_state=prev)

    assert outcome.routing_bypass is True
    assert set(_names(outcome.tool_schemas)) == {"tool_c", "delegate"}
    embedder.embed_query.assert_not_called()


async def test_routing_inactivo_no_duplica_pinned():
    """Con pocas tools el routing pasa todo — la unión debe ser no-op."""
    settings = _make_settings(tools_min_tools=10)
    embedder, skills, tools = _make_ports(
        all_tools=["tool_a", "delegate"],
        routed=[],
    )

    outcome = await _run(settings, embedder, skills, tools)

    assert _names(outcome.tool_schemas) == ["tool_a", "delegate"]


async def test_tools_override_es_autoridad_del_caller():
    """Un override explícito (scheduler) NO se toca: sin unión de pinned."""
    settings = _make_settings()
    embedder, skills, tools = _make_ports(
        all_tools=["tool_a", "tool_b", "tool_c", "delegate"],
        routed=["tool_a"],
    )
    override = [_schema("tool_b")]

    outcome = await _run(settings, embedder, skills, tools, tools_override=override)

    assert _names(outcome.tool_schemas) == ["tool_b"]


async def test_sin_pinned_comportamiento_legacy():
    settings = _make_settings(tools_pinned=frozenset())
    embedder, skills, tools = _make_ports(
        all_tools=["tool_a", "tool_b", "tool_c", "delegate"],
        routed=["tool_a", "tool_b"],
    )

    outcome = await _run(settings, embedder, skills, tools)

    assert set(_names(outcome.tool_schemas)) == {"tool_a", "tool_b"}
