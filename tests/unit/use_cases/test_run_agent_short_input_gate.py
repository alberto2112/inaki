"""Tests — gate por cantidad de palabras para bypass del RAG.

Cubre la política ``rag.min_words_threshold``: si el user_input es corto y
existe selección sticky previa, el turno no calcula embedding, no toca TTL
y hereda la selección del turno anterior intacta.
"""

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
    RagConfig,
    SkillsConfig,
    ToolsConfig,
)


def _make_use_case(
    *,
    rag: RagConfig,
    skills: SkillsConfig,
    tools: ToolsConfig,
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
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memory=MemoryConfig(db_filename=":memory:", default_top_k=3),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
        skills=skills,
        tools=tools,
        rag=rag,
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
# Feature desactivada (threshold = 0) — comportamiento histórico intacto
# ---------------------------------------------------------------------------


async def test_threshold_zero_runs_rag_even_on_short_input_with_sticky(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """threshold=0 desactiva la feature: el RAG corre siempre aunque haya sticky previo."""
    s = Skill(id="agenda", name="agenda", description="d")
    mock_skills.list_all.return_value = [s]
    mock_skills.retrieve.return_value = [s]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState(sticky_skills={"agenda": 2})

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=0),
        skills=SkillsConfig(rag_min_skills=0, sticky_ttl=3),
        tools=ToolsConfig(rag_min_tools=10),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    await uc.execute("si")  # input ultra corto

    mock_embedder.embed_query.assert_called_once()


# ---------------------------------------------------------------------------
# Feature activada — input corto con sticky previo → bypass
# ---------------------------------------------------------------------------


async def test_short_input_with_sticky_skills_bypasses_embedder(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Input corto + sticky previo → NO se llama al embedder."""
    s = Skill(id="agenda", name="agenda", description="d")
    mock_skills.list_all.return_value = [s]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState(sticky_skills={"agenda": 2})

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=5),
        skills=SkillsConfig(rag_min_skills=0, sticky_ttl=3),
        tools=ToolsConfig(rag_min_tools=10),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    await uc.execute("y eso?")  # 2 palabras

    mock_embedder.embed_query.assert_not_called()


async def test_short_input_inherits_sticky_skills_into_system_prompt(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """La skill heredada del sticky previo llega al system prompt."""
    s_agenda = Skill(id="agenda", name="agenda", description="Consulta agenda")
    s_poema = Skill(id="poema", name="poema", description="Escribe poemas")
    mock_skills.list_all.return_value = [s_agenda, s_poema]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState(sticky_skills={"agenda": 2})

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=5),
        skills=SkillsConfig(rag_min_skills=0, sticky_ttl=3),
        tools=ToolsConfig(rag_min_tools=10),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    await uc.execute("dale")

    system_prompt = mock_llm.complete.call_args.args[1]
    assert "agenda" in system_prompt
    # poema NO quedó sticky → NO debería estar en el prompt de un turno corto con bypass
    assert "poema" not in system_prompt


async def test_short_input_inherits_sticky_tools_into_tool_loop(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Las tools heredadas del sticky previo llegan al tool_loop intactas."""
    mock_skills.list_all.return_value = []
    all_schemas = [_tool_schema("list_events"), _tool_schema("read_file")]
    mock_tools.get_schemas.return_value = all_schemas
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState(sticky_tools={"list_events": 2})

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=5),
        skills=SkillsConfig(rag_min_skills=10),
        tools=ToolsConfig(rag_min_tools=0, sticky_ttl=4),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    with patch(
        "core.use_cases.run_agent.run_tool_loop", new=AsyncMock(return_value="ok")
    ) as mock_loop:
        await uc.execute("y eso?")

    passed_names = {s["function"]["name"] for s in mock_loop.call_args.kwargs["tool_schemas"]}
    assert passed_names == {"list_events"}
    mock_embedder.embed_query.assert_not_called()


async def test_short_input_bypass_does_not_persist_state(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """El bypass NO toca el TTL ni persiste estado — la selección queda congelada."""
    s = Skill(id="agenda", name="agenda", description="d")
    mock_skills.list_all.return_value = [s]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState(sticky_skills={"agenda": 2})

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=5),
        skills=SkillsConfig(rag_min_skills=0, sticky_ttl=3),
        tools=ToolsConfig(rag_min_tools=10),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    await uc.execute("dale")

    mock_history.save_state.assert_not_called()


async def test_short_input_bypass_filters_ghost_sticky_ids(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Un id sticky que ya no está en el catálogo se descarta sin crashear."""
    s_only = Skill(id="existe", name="existe", description="d")
    mock_skills.list_all.return_value = [s_only]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState(
        sticky_skills={"existe": 2, "fantasma": 2}
    )

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=5),
        skills=SkillsConfig(rag_min_skills=0, sticky_ttl=3),
        tools=ToolsConfig(rag_min_tools=10),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    await uc.execute("ok")

    system_prompt = mock_llm.complete.call_args.args[1]
    assert "existe" in system_prompt
    # "fantasma" no existe → no debería aparecer (además no crashea)


# ---------------------------------------------------------------------------
# Feature activada — casos donde el RAG SÍ corre
# ---------------------------------------------------------------------------


async def test_short_input_without_sticky_runs_rag_normally(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Primer turno (sticky vacío) con input corto → el RAG corre igual.

    Sin sticky previo no hay contexto del cual heredar; el bypass no aplica.
    """
    s = Skill(id="agenda", name="agenda", description="d")
    mock_skills.list_all.return_value = [s]
    mock_skills.retrieve.return_value = [s]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState()  # sticky vacío

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=5),
        skills=SkillsConfig(rag_min_skills=0, sticky_ttl=3),
        tools=ToolsConfig(rag_min_tools=10),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    await uc.execute("hola")  # 1 palabra, pero no hay sticky

    mock_embedder.embed_query.assert_called_once()


async def test_long_input_runs_rag_even_with_sticky(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Input largo → el RAG corre siempre, sin importar el sticky previo."""
    s = Skill(id="agenda", name="agenda", description="d")
    mock_skills.list_all.return_value = [s]
    mock_skills.retrieve.return_value = [s]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState(sticky_skills={"agenda": 2})

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=5),
        skills=SkillsConfig(rag_min_skills=0, sticky_ttl=3),
        tools=ToolsConfig(rag_min_tools=10),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    await uc.execute("contame qué tenés agendado para mañana a la tarde")

    mock_embedder.embed_query.assert_called_once()


async def test_threshold_is_strict_less_than(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """Con threshold=5, un input de EXACTAMENTE 5 palabras NO es corto → RAG corre."""
    s = Skill(id="agenda", name="agenda", description="d")
    mock_skills.list_all.return_value = [s]
    mock_skills.retrieve.return_value = [s]
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    mock_history.load_state.return_value = ConversationState(sticky_skills={"agenda": 2})

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=5),
        skills=SkillsConfig(rag_min_skills=0, sticky_ttl=3),
        tools=ToolsConfig(rag_min_tools=10),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    await uc.execute("uno dos tres cuatro cinco")  # exactamente 5

    mock_embedder.embed_query.assert_called_once()


# ---------------------------------------------------------------------------
# inspect() — espejo de execute() (no persiste estado, no llama LLM)
# ---------------------------------------------------------------------------


async def test_inspect_short_input_with_sticky_bypasses_embedder(
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """inspect() respeta el mismo gate: input corto + sticky → no llama embedder."""
    s_agenda = Skill(id="agenda", name="agenda", description="Consulta agenda")
    s_poema = Skill(id="poema", name="poema", description="Escribe poemas")
    mock_skills.list_all.return_value = [s_agenda, s_poema]
    mock_tools.get_schemas.return_value = []
    mock_history.load_state.return_value = ConversationState(sticky_skills={"agenda": 2})

    uc = _make_use_case(
        rag=RagConfig(min_words_threshold=5),
        skills=SkillsConfig(rag_min_skills=0, sticky_ttl=3),
        tools=ToolsConfig(rag_min_tools=10),
        mock_llm=mock_llm,
        mock_memory=mock_memory,
        mock_embedder=mock_embedder,
        mock_skills=mock_skills,
        mock_history=mock_history,
        mock_tools=mock_tools,
    )
    result = await uc.inspect("dale")

    mock_embedder.embed_query.assert_not_called()
    selected_ids = {s.id for s in result.selected_skills}
    assert selected_ids == {"agenda"}
