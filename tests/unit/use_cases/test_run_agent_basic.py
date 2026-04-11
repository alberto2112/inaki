"""Tests unitarios para RunAgentUseCase — flujo básico."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

from core.use_cases.run_agent import RunAgentUseCase, InspectResult
from core.domain.entities.message import Message, Role
from core.domain.entities.skill import Skill
from infrastructure.config import (
    AgentConfig,
    LLMConfig,
    EmbeddingConfig,
    MemoryConfig,
    HistoryConfig,
    SkillsConfig,
    ToolsConfig,
)


@pytest.fixture
def use_case(agent_config, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools):
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
    mock_llm.complete.return_value = "Hola, soy Iñaki"
    response = await use_case.execute("Hola")
    assert response == "Hola, soy Iñaki"


async def test_execute_persists_user_and_assistant_messages(use_case, mock_llm, mock_history):
    mock_llm.complete.return_value = "Respuesta"
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


async def test_execute_does_not_call_embed_query_when_rag_inactive(use_case, mock_embedder, mock_skills):
    # Con rag_min_skills=10 y lista vacía de skills, el flag RAG es falso → embed_query no se llama
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

def _make_use_case(overrides: dict, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools) -> RunAgentUseCase:
    """Construye un RunAgentUseCase con AgentConfig parcialmente sobreescrita."""
    cfg = AgentConfig(
        id="test",
        name="Test Agent",
        description="Agente de test",
        system_prompt="Eres un asistente de test.",
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_path="models/test"),
        memory=overrides.get("memory", MemoryConfig(db_path=":memory:", default_top_k=3)),
        history=HistoryConfig(db_path="/tmp/inaki_test/history.db"),
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


async def test_embed_query_zero_calls_when_both_rag_flags_false(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """SC-01, FR-01, AC-01 — embed_query no se llama cuando ambos flags RAG están inactivos."""
    # rag_min_skills=10, lista vacía → skills_rag_active=False
    # rag_min_tools=10,  schemas vacíos → tools_rag_active=False
    mock_skills.list_all.return_value = []
    mock_tools.get_schemas.return_value = []
    uc = _make_use_case(
        {"skills": SkillsConfig(rag_min_skills=10), "tools": ToolsConfig(rag_min_tools=10)},
        mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools,
    )
    await uc.execute("hola")
    assert mock_embedder.embed_query.call_count == 0


async def test_embed_query_called_when_skills_rag_active(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """SC-02, AC-01 — embed_query se llama cuando skills RAG está activo."""
    # rag_min_skills=0 → con 1 skill la lista supera el umbral
    skill = Skill(id="s1", name="skill1", description="desc1")
    mock_skills.list_all.return_value = [skill]
    mock_skills.retrieve.return_value = [skill]
    mock_tools.get_schemas.return_value = []
    uc = _make_use_case(
        {"skills": SkillsConfig(rag_min_skills=0), "tools": ToolsConfig(rag_min_tools=10)},
        mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools,
    )
    await uc.execute("hola")
    assert mock_embedder.embed_query.call_count == 1


async def test_memory_search_not_called_in_execute(
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools, agent_config
):
    """SC-07, FR-04, AC-02 — memory.search no se llama en execute."""
    mock_skills.list_all.return_value = []
    uc = RunAgentUseCase(
        llm=mock_llm, memory=mock_memory, embedder=mock_embedder,
        skills=mock_skills, history=mock_history, tools=mock_tools,
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
        llm=mock_llm, memory=mock_memory, embedder=mock_embedder,
        skills=mock_skills, history=mock_history, tools=mock_tools,
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
    mem_cfg = MemoryConfig(db_path=":memory:", default_top_k=3, digest_path=str(digest_file))
    uc = _make_use_case(
        {"memory": mem_cfg},
        mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools,
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
    mem_cfg = MemoryConfig(db_path=":memory:", default_top_k=3, digest_path=str(nonexistent))
    uc = _make_use_case(
        {"memory": mem_cfg},
        mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools,
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
        llm=mock_llm, memory=mock_memory, embedder=mock_embedder,
        skills=mock_skills, history=mock_history, tools=mock_tools,
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
        llm=mock_llm, memory=mock_memory, embedder=mock_embedder,
        skills=mock_skills, history=mock_history, tools=mock_tools,
        agent_config=agent_config,
    )
    with patch.object(
        Path, "read_text",
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
        llm=mock_llm, memory=mock_memory, embedder=mock_embedder,
        skills=mock_skills, history=mock_history, tools=mock_tools,
        agent_config=agent_config,
    )
    result = await uc.inspect("hola")
    assert hasattr(result, "memory_digest") and isinstance(result.memory_digest, str)
    assert not hasattr(result, "memories")
