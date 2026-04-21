"""Tests de integración — Sticky Union con TTL en RunAgentUseCase."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch


from core.domain.entities.skill import Skill
from core.domain.value_objects.conversation_state import ConversationState
from core.domain.value_objects.llm_response import LLMResponse
from core.use_cases.run_agent import RunAgentUseCase
from infrastructure.config import (
    AgentConfig,
    ChatHistoryConfig,
    EmbeddingConfig,
    LLMConfig,
    MemoryConfig,
    SkillsConfig,
    ToolsConfig,
)


def _make_use_case(
    overrides: dict,
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
) -> RunAgentUseCase:
    cfg = AgentConfig(
        id="test",
        name="Test Agent",
        description="Agente de test",
        system_prompt="Eres un asistente de test.",
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memory=MemoryConfig(db_filename=":memory:", default_top_k=3),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
        skills=overrides.get("skills", SkillsConfig()),
        tools=overrides.get("tools", ToolsConfig()),
    )
    return RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=cfg,
    )


def _tool_schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": f"{name} desc"}}


# ---------------------------------------------------------------------------
# Feature disabled (sticky_ttl = 0) — backwards compat
# ---------------------------------------------------------------------------


async def test_sticky_disabled_does_not_save_state_with_routing_inactive(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Con routing inactivo y sticky_ttl=0 (deshabilitado), save_state NO debe llamarse."""
    mock_skills.list_all.return_value = []
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = _make_use_case(
        {
            "skills": SkillsConfig(semantic_routing_min_skills=10, sticky_ttl=0),
            "tools": ToolsConfig(semantic_routing_min_tools=10, sticky_ttl=0),
        },
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("hola")

    mock_history.save_state.assert_not_called()


async def test_sticky_disabled_skills_routing_active_saves_empty_state(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """
    Con sticky_ttl=0 pero routing activo, save_state SÍ se llama (el pipeline pasó por
    la rama sticky) pero con estado vacío — el feature-flag desactivado se traduce
    en "no acumular estado".
    """
    s1 = Skill(id="s1", name="s1", description="d")
    mock_skills.list_all.return_value = [s1]
    mock_skills.retrieve.return_value = [s1]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = _make_use_case(
        {"skills": SkillsConfig(semantic_routing_min_skills=0, sticky_ttl=0)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("hola")

    mock_history.save_state.assert_called_once()
    saved_state = mock_history.save_state.call_args.args[1]
    assert saved_state.sticky_skills == {}


# ---------------------------------------------------------------------------
# Sticky on skills — basic flow
# ---------------------------------------------------------------------------


async def test_sticky_skills_persists_new_ttls_on_routing_selection(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Cuando el routing selecciona una skill con sticky_ttl=3, se persiste con TTL=3."""
    s_agenda = Skill(id="agenda", name="agenda", description="Consulta agenda")
    mock_skills.list_all.return_value = [s_agenda]
    mock_skills.retrieve.return_value = [s_agenda]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = _make_use_case(
        {"skills": SkillsConfig(semantic_routing_min_skills=0, sticky_ttl=3)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("mira mi agenda")

    saved_state: ConversationState = mock_history.save_state.call_args.args[1]
    assert saved_state.sticky_skills == {"agenda": 3}


async def test_sticky_skill_survives_turn_when_routing_does_not_reselect(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """
    Turno N-1 seleccionó "agenda" con TTL=3. Turno N el routing devuelve 0 skills
    (follow-up ambiguo). El system prompt debe recibir "agenda" igual.
    """
    s_agenda = Skill(id="agenda", name="agenda", description="Consulta agenda")
    mock_skills.list_all.return_value = [s_agenda]
    mock_skills.retrieve.return_value = []  # routing no selecciona nada
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    # Estado previo: "agenda" sticky con TTL=3
    mock_history.load_state.return_value = ConversationState(sticky_skills={"agenda": 3})

    uc = _make_use_case(
        {"skills": SkillsConfig(semantic_routing_min_skills=0, sticky_ttl=3)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("y del siguiente?")

    # TTL decrementa: 3 -> 2
    saved_state: ConversationState = mock_history.save_state.call_args.args[1]
    assert saved_state.sticky_skills == {"agenda": 2}


async def test_sticky_skill_refreshes_ttl_when_routing_reselects(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Si el routing vuelve a seleccionar la skill sticky, su TTL se refresca."""
    s_agenda = Skill(id="agenda", name="agenda", description="Consulta agenda")
    mock_skills.list_all.return_value = [s_agenda]
    mock_skills.retrieve.return_value = [s_agenda]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    mock_history.load_state.return_value = ConversationState(
        sticky_skills={"agenda": 1}  # a punto de expirar
    )

    uc = _make_use_case(
        {"skills": SkillsConfig(semantic_routing_min_skills=0, sticky_ttl=5)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("de nuevo agenda")

    saved_state: ConversationState = mock_history.save_state.call_args.args[1]
    assert saved_state.sticky_skills == {"agenda": 5}


async def test_sticky_skill_expires_after_ttl_turns(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """TTL=1 sin re-selección → la skill desaparece del sticky state."""
    s_agenda = Skill(id="agenda", name="agenda", description="Consulta agenda")
    mock_skills.list_all.return_value = [s_agenda]
    mock_skills.retrieve.return_value = []
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    mock_history.load_state.return_value = ConversationState(sticky_skills={"agenda": 1})

    uc = _make_use_case(
        {"skills": SkillsConfig(semantic_routing_min_skills=0, sticky_ttl=3)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("otra cosa")

    saved_state: ConversationState = mock_history.save_state.call_args.args[1]
    assert saved_state.sticky_skills == {}


async def test_sticky_union_passes_sticky_skill_to_llm_context(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """
    Verificación end-to-end: una skill sticky supervivient aparece en el
    AgentContext que se usa para construir el system prompt.
    """
    s_agenda = Skill(id="agenda", name="agenda", description="Consulta agenda")
    s_poema = Skill(id="poema", name="poema", description="Escribe poemas")
    mock_skills.list_all.return_value = [s_agenda, s_poema]
    mock_skills.retrieve.return_value = [s_poema]  # routing elige poema ahora
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    mock_history.load_state.return_value = ConversationState(
        sticky_skills={"agenda": 2}  # agenda sobrevive
    )

    uc = _make_use_case(
        {"skills": SkillsConfig(semantic_routing_min_skills=0, sticky_ttl=3)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("escribí un poema")

    system_prompt = mock_llm.complete.call_args.args[1]
    assert "agenda" in system_prompt
    assert "poema" in system_prompt


async def test_sticky_skill_filters_ghost_ids(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """
    Si el state tiene un sticky con id que ya no existe en el catálogo actual
    (skill borrada/renombrada), debe filtrarse silenciosamente al materializar.
    """
    s_only = Skill(id="existe", name="existe", description="d")
    mock_skills.list_all.return_value = [s_only]
    mock_skills.retrieve.return_value = []
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    mock_history.load_state.return_value = ConversationState(
        sticky_skills={"existe": 2, "fantasma": 2}
    )

    uc = _make_use_case(
        {"skills": SkillsConfig(semantic_routing_min_skills=0, sticky_ttl=3)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    # No debe crashear
    await uc.execute("algo")

    system_prompt = mock_llm.complete.call_args.args[1]
    assert "existe" in system_prompt
    # "fantasma" NO aparece en el prompt (no hay skill con ese id)


# ---------------------------------------------------------------------------
# Sticky on tools
# ---------------------------------------------------------------------------


async def test_sticky_tools_persists_new_ttls(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """routing selecciona una tool → se marca sticky con TTL configurado."""
    mock_skills.list_all.return_value = []
    all_schemas = [_tool_schema("list_events"), _tool_schema("read_file")]
    mock_tools.get_schemas.return_value = all_schemas
    mock_tools.get_schemas_relevant.return_value = [_tool_schema("list_events")]
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = _make_use_case(
        {"tools": ToolsConfig(semantic_routing_min_tools=0, sticky_ttl=4)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    with patch("core.use_cases.run_agent.run_tool_loop", new=AsyncMock(return_value="ok")):
        await uc.execute("mis eventos del 27")

    saved_state: ConversationState = mock_history.save_state.call_args.args[1]
    assert saved_state.sticky_tools == {"list_events": 4}


async def test_sticky_tool_survives_ambiguous_followup(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Tool sticky sobrevive turno donde el routing no la re-selecciona."""
    mock_skills.list_all.return_value = []
    all_schemas = [_tool_schema("list_events"), _tool_schema("read_file")]
    mock_tools.get_schemas.return_value = all_schemas
    mock_tools.get_schemas_relevant.return_value = []  # routing vacío en follow-up
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    mock_history.load_state.return_value = ConversationState(sticky_tools={"list_events": 2})

    uc = _make_use_case(
        {"tools": ToolsConfig(semantic_routing_min_tools=0, sticky_ttl=4)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    with patch(
        "core.use_cases.run_agent.run_tool_loop", new=AsyncMock(return_value="ok")
    ) as mock_loop:
        await uc.execute("y del siguiente?")

    # La tool sticky llega al tool_loop
    passed_schemas = mock_loop.call_args.kwargs["tool_schemas"]
    passed_names = {s["function"]["name"] for s in passed_schemas}
    assert "list_events" in passed_names

    # TTL decrementa
    saved_state: ConversationState = mock_history.save_state.call_args.args[1]
    assert saved_state.sticky_tools == {"list_events": 1}


async def test_sticky_tool_filters_ghost_names(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Una tool sticky que ya no está en el catálogo se descarta sin crashear."""
    mock_skills.list_all.return_value = []
    all_schemas = [_tool_schema("existe")]
    mock_tools.get_schemas.return_value = all_schemas
    mock_tools.get_schemas_relevant.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    mock_history.load_state.return_value = ConversationState(
        sticky_tools={"existe": 2, "fantasma": 2}
    )

    uc = _make_use_case(
        {"tools": ToolsConfig(semantic_routing_min_tools=0, sticky_ttl=3)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    with patch(
        "core.use_cases.run_agent.run_tool_loop", new=AsyncMock(return_value="ok")
    ) as mock_loop:
        await uc.execute("algo")

    passed_names = {s["function"]["name"] for s in mock_loop.call_args.kwargs["tool_schemas"]}
    assert passed_names == {"existe"}


# ---------------------------------------------------------------------------
# tools_override bypass
# ---------------------------------------------------------------------------


async def test_tools_override_bypasses_tools_sticky_but_skills_still_apply(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """
    Con tools_override activo, la lógica sticky de tools NO aplica (override
    manda). Pero la sticky de skills sigue funcionando normalmente.
    """
    s_agenda = Skill(id="agenda", name="agenda", description="d")
    mock_skills.list_all.return_value = [s_agenda]
    mock_skills.retrieve.return_value = [s_agenda]
    mock_tools.get_schemas.return_value = [_tool_schema("any")]
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = _make_use_case(
        {
            "skills": SkillsConfig(semantic_routing_min_skills=0, sticky_ttl=3),
            "tools": ToolsConfig(semantic_routing_min_tools=0, sticky_ttl=5),
        },
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )

    override = [_tool_schema("solo_esta")]
    with patch(
        "core.use_cases.run_agent.run_tool_loop", new=AsyncMock(return_value="ok")
    ) as mock_loop:
        await uc.execute("hola", tools_override=override)

    # routing de tools nunca se llama
    mock_tools.get_schemas_relevant.assert_not_called()

    # El override llega intacto al tool_loop
    assert mock_loop.call_args.kwargs["tool_schemas"] == override

    # Las skills SÍ quedan sticky
    saved_state: ConversationState = mock_history.save_state.call_args.args[1]
    assert saved_state.sticky_skills == {"agenda": 3}
    # Tools no se tocan: state de tools queda como estaba (vacío, no hubo state previo)
    assert saved_state.sticky_tools == {}


# ---------------------------------------------------------------------------
# Ordering — state persiste después del tool loop, antes del append
# ---------------------------------------------------------------------------


async def test_save_state_called_before_message_append(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """
    Contrato de orden: save_state debe ejecutarse ANTES de history.append, para
    que el state de este turno esté persistido antes de que los mensajes lo estén.
    """
    s = Skill(id="s1", name="s1", description="d")
    mock_skills.list_all.return_value = [s]
    mock_skills.retrieve.return_value = [s]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    call_order: list[str] = []
    mock_history.save_state.side_effect = lambda *a, **k: call_order.append("save_state")
    mock_history.append.side_effect = lambda *a, **k: call_order.append("append")

    uc = _make_use_case(
        {"skills": SkillsConfig(semantic_routing_min_skills=0, sticky_ttl=3)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("hola")

    # save_state primero, luego los dos append
    assert call_order == ["save_state", "append", "append"]
