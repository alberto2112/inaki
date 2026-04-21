"""Tests unitarios para RunAgentUseCase — flujo básico."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.use_cases.run_agent import RunAgentUseCase
from core.domain.entities.message import Message, Role
from core.domain.entities.skill import Skill
from core.domain.value_objects.llm_response import LLMResponse
from infrastructure.config import (
    AgentConfig,
    LLMConfig,
    EmbeddingConfig,
    MemoryConfig,
    ChatHistoryConfig,
    SkillsConfig,
    ToolsConfig,
)


@pytest.fixture
def use_case(
    agent_config, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    return RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )


async def test_execute_returns_llm_response(use_case, mock_llm):
    mock_llm.complete.return_value = LLMResponse.of_text("Hola, soy Iñaki")
    response = await use_case.execute("Hola")
    assert response == "Hola, soy Iñaki"


async def test_execute_persists_user_and_assistant_messages(use_case, mock_llm, mock_history):
    mock_llm.complete.return_value = LLMResponse.of_text("Respuesta")
    await use_case.execute("Hola")

    calls = mock_history.append.call_args_list
    assert len(calls) == 2
    user_msg = calls[0].args[1]
    assistant_msg = calls[1].args[1]
    assert user_msg.role == Role.USER
    assert user_msg.content == "Hola"
    assert assistant_msg.role == Role.ASSISTANT
    assert assistant_msg.content == "Respuesta"


async def test_execute_loads_history_before_calling_llm(use_case, mock_history, mock_llm):
    existing = [Message(role=Role.USER, content="mensaje previo")]
    mock_history.load.return_value = existing
    await use_case.execute("nuevo mensaje")

    mock_history.load.assert_called_once_with("test")
    # El LLM recibe el historial cargado + el nuevo mensaje
    call_args = mock_llm.complete.call_args
    messages_passed = call_args.args[0]
    assert any(m.content == "mensaje previo" for m in messages_passed)
    assert any(m.content == "nuevo mensaje" for m in messages_passed)


async def test_execute_does_not_call_embed_query_when_routing_inactive(
    use_case, mock_embedder, mock_skills
):
    # Con semantic_routing_min_skills=10 y lista vacía de skills, el flag routing es falso → embed_query no se llama
    mock_skills.list_all.return_value = []
    await use_case.execute("test input")
    mock_embedder.embed_query.assert_not_called()


async def test_execute_does_not_call_memory_search(use_case, mock_memory):
    # memory.search ya no forma parte del hot path — el digest se lee de disco
    await use_case.execute("test")
    mock_memory.search.assert_not_called()


# ---------------------------------------------------------------------------
# Nuevos tests — Phase 4 (memory-digest-markdown)
# ---------------------------------------------------------------------------


def _make_use_case(
    overrides: dict, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
) -> RunAgentUseCase:
    """Construye un RunAgentUseCase con AgentConfig parcialmente sobreescrita."""
    cfg = AgentConfig(
        id="test",
        name="Test Agent",
        description="Agente de test",
        system_prompt="Eres un asistente de test.",
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memory=overrides.get("memory", MemoryConfig(db_filename=":memory:", default_top_k=3)),
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


async def test_embed_query_zero_calls_when_both_routing_flags_false(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """SC-01, FR-01, AC-01 — embed_query no se llama cuando ambos flags routing están inactivos."""
    # semantic_routing_min_skills=10, lista vacía → skills_routing_active=False
    # semantic_routing_min_tools=10,  schemas vacíos → tools_routing_active=False
    mock_skills.list_all.return_value = []
    mock_tools.get_schemas.return_value = []
    uc = _make_use_case(
        {
            "skills": SkillsConfig(semantic_routing_min_skills=10),
            "tools": ToolsConfig(semantic_routing_min_tools=10),
        },
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("hola")
    assert mock_embedder.embed_query.call_count == 0


async def test_embed_query_called_when_skills_routing_active(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """SC-02, AC-01 — embed_query se llama cuando skills routing está activo."""
    # semantic_routing_min_skills=0 → con 1 skill la lista supera el umbral
    skill = Skill(id="s1", name="skill1", description="desc1")
    mock_skills.list_all.return_value = [skill]
    mock_skills.retrieve.return_value = [skill]
    mock_tools.get_schemas.return_value = []
    uc = _make_use_case(
        {
            "skills": SkillsConfig(semantic_routing_min_skills=0),
            "tools": ToolsConfig(semantic_routing_min_tools=10),
        },
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("hola")
    assert mock_embedder.embed_query.call_count == 1


async def test_memory_search_not_called_in_execute(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools, agent_config
):
    """SC-07, FR-04, AC-02 — memory.search no se llama en execute."""
    mock_skills.list_all.return_value = []
    uc = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )
    await uc.execute("hola")
    assert mock_memory.search.call_count == 0


async def test_memory_search_not_called_in_inspect(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools, agent_config
):
    """SC-08, FR-04, AC-02 — memory.search no se llama en inspect."""
    mock_skills.list_all.return_value = []
    uc = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )
    await uc.inspect("hola")
    assert mock_memory.search.call_count == 0


async def test_digest_present_injected_into_system_prompt(
    tmp_path, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """SC-05, AC-03 — cuando el digest existe, su contenido aparece en el system prompt."""
    digest_file = tmp_path / "last_memories.md"
    digest_file.write_text("# Test digest\n- [2026-04-09] Hello", encoding="utf-8")

    mock_skills.list_all.return_value = []
    mem_cfg = MemoryConfig(
        db_filename=":memory:", default_top_k=3, digest_filename=str(digest_file)
    )
    uc = _make_use_case(
        {"memory": mem_cfg},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    await uc.execute("hola")

    call_args = mock_llm.complete.call_args
    captured_prompt = call_args.args[1]
    assert "# Test digest" in captured_prompt
    assert "Hello" in captured_prompt


async def test_digest_absent_no_exception(
    tmp_path, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """SC-06, FR-10, SC-18, AC-03 — sin digest, no hay excepción y el system prompt no tiene placeholder."""
    nonexistent = tmp_path / "does_not_exist.md"

    mock_skills.list_all.return_value = []
    mem_cfg = MemoryConfig(
        db_filename=":memory:", default_top_k=3, digest_filename=str(nonexistent)
    )
    uc = _make_use_case(
        {"memory": mem_cfg},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )
    # No debe lanzar excepción
    await uc.execute("hola")

    call_args = mock_llm.complete.call_args
    captured_prompt = call_args.args[1]
    assert "digest" not in captured_prompt.lower()
    assert "MISSING" not in captured_prompt


async def test_read_digest_swallows_oserror(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools, agent_config
):
    """NFR-03, FR-03 — _read_digest retorna '' y no propaga PermissionError."""
    mock_skills.list_all.return_value = []
    uc = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )
    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        result = uc._read_digest()
    assert result == ""


async def test_read_digest_returns_empty_on_unicode_decode_error(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools, agent_config
):
    """NFR-03 (archive fix, Warning 4) — _read_digest retorna '' y no propaga UnicodeDecodeError."""
    mock_skills.list_all.return_value = []
    uc = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )
    with patch.object(
        Path,
        "read_text",
        side_effect=UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte"),
    ):
        result = uc._read_digest()
    assert result == ""


async def test_inspect_result_has_memory_digest_not_memories(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools, agent_config
):
    """SC-17, AC-07 — InspectResult tiene memory_digest (str), no tiene memories."""
    mock_skills.list_all.return_value = []
    uc = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )
    result = await uc.inspect("hola")
    assert hasattr(result, "memory_digest") and isinstance(result.memory_digest, str)
    assert not hasattr(result, "memories")


# ---------------------------------------------------------------------------
# Task 6.1 — extra_sections thread-through
# ---------------------------------------------------------------------------


async def test_extra_system_sections_threaded_to_llm(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools, agent_config
):
    """
    Task 6.1 thread-through verification.

    When _extra_system_sections is set on RunAgentUseCase, the content MUST
    appear in the system_prompt passed to the LLM on the next execute() call.
    """
    mock_skills.list_all.return_value = []
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )

    # Simulate what wire_delegation does
    uc.set_extra_system_sections(["SECTION-TEST: discovery content here"])

    await uc.execute("hola")

    call_args = mock_llm.complete.call_args
    # LLM.complete(messages, system_prompt) — system_prompt is the second positional arg
    captured_prompt = call_args.args[1]
    assert "SECTION-TEST: discovery content here" in captured_prompt, (
        "extra_sections content must appear in the system prompt passed to the LLM"
    )


async def test_extra_system_sections_empty_by_default(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools, agent_config
):
    """
    When no extra sections are set, _extra_system_sections is empty and
    execute() works normally without any extra content in the prompt.
    """
    mock_skills.list_all.return_value = []
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )

    # No extra sections set — _extra_system_sections defaults to []
    assert uc._extra_system_sections == []

    # execute() must work normally
    result = await uc.execute("hola")
    assert result == "ok"


async def test_set_extra_system_sections_replaces_existing(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools, agent_config
):
    """
    set_extra_system_sections replaces the existing list (idempotent setter).
    The last set value is what appears in the system prompt.
    """
    mock_skills.list_all.return_value = []
    mock_tools.get_schemas.return_value = []
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
    )

    uc.set_extra_system_sections(["FIRST-SECTION"])
    uc.set_extra_system_sections(["SECOND-SECTION"])

    await uc.execute("hola")

    captured_prompt = mock_llm.complete.call_args.args[1]
    assert "SECOND-SECTION" in captured_prompt
    assert "FIRST-SECTION" not in captured_prompt, (
        "First section must have been replaced by the second"
    )


# ---------------------------------------------------------------------------
# tools_override — usado por triggers agent_send del scheduler
# ---------------------------------------------------------------------------


async def test_execute_tools_override_forces_schemas_and_bypasses_rag(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """
    Cuando se provee ``tools_override``, ``run_tool_loop`` recibe esas schemas
    exactas y la selección routing de tools se bypasea (get_schemas_relevant NO se
    llama aunque el umbral routing esté activo).
    """
    mock_skills.list_all.return_value = []
    # Muchas tool schemas "reales" — normalmente activaría routing
    mock_tools.get_schemas.return_value = [{"name": f"tool_{i}"} for i in range(20)]
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = _make_use_case(
        {"tools": ToolsConfig(semantic_routing_min_tools=5)},
        mock_llm,
        mock_memory,
        mock_embedder,
        mock_skills,
        mock_history,
        mock_tools,
    )

    override = [{"name": "solo_esta_tool"}]
    with patch(
        "core.use_cases.run_agent.run_tool_loop", new=AsyncMock(return_value="ok")
    ) as mock_loop:
        await uc.execute("hola", tools_override=override)

    # routing de tools NO debe haberse disparado
    mock_tools.get_schemas_relevant.assert_not_called()
    # run_tool_loop recibe exactamente el override
    passed_schemas = mock_loop.call_args.kwargs["tool_schemas"]
    assert passed_schemas == override


async def test_execute_no_override_uses_full_schemas_when_routing_inactive(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """
    Sin ``tools_override``, el comportamiento previo se preserva: cuando routing
    de tools está inactivo, se usa ``get_schemas()`` completo.
    """
    mock_skills.list_all.return_value = []
    all_schemas = [{"name": "tool_a"}, {"name": "tool_b"}]
    mock_tools.get_schemas.return_value = all_schemas
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = _make_use_case(
        {"tools": ToolsConfig(semantic_routing_min_tools=10)},  # umbral alto → routing inactivo
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
        await uc.execute("hola")

    mock_tools.get_schemas_relevant.assert_not_called()
    assert mock_loop.call_args.kwargs["tool_schemas"] == all_schemas


async def test_execute_tools_override_empty_list_disables_all_tools(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """
    ``tools_override=[]`` es distinto de ``None``: fuerza al agente a correr
    sin ninguna tool. Caso de uso: tarea programada que solo genera texto.
    """
    mock_skills.list_all.return_value = []
    mock_tools.get_schemas.return_value = [{"name": "tool_a"}]
    mock_llm.complete.return_value = LLMResponse.of_text("ok")

    uc = _make_use_case(
        {
            "tools": ToolsConfig(semantic_routing_min_tools=0)
        },  # routing activo si override fuera None
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
        await uc.execute("hola", tools_override=[])

    mock_tools.get_schemas_relevant.assert_not_called()
    assert mock_loop.call_args.kwargs["tool_schemas"] == []
