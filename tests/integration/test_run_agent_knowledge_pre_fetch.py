"""
Test de integración: pre-fetch de knowledge popula AgentContext.knowledge_chunks.

Usa SQLite real (en memoria) para MemoryRepository y KnowledgeOrchestrator.
El LLM y el embedder son mocks controlados.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.outbound.knowledge.sqlite_memory_knowledge_source import (
    SqliteMemoryKnowledgeSource,
)
from adapters.outbound.memory.sqlite_memory_repo import SQLiteMemoryRepository
from core.domain.entities.memory import MemoryEntry
from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator
from core.domain.value_objects.llm_response import LLMResponse
from core.use_cases.run_agent import RunAgentUseCase


def _vec_normalizado(n: int = 384, value: float = 1.0) -> list[float]:
    """Vector unitario simple de dimensión n."""
    norma = math.sqrt(n * value**2)
    return [value / norma] * n


@pytest.fixture
def db_memory(tmp_path: Path) -> SQLiteMemoryRepository:
    """Repositorio de memoria real con SQLite en disco temporal."""
    embedder_mock = MagicMock()
    embedder_mock.embed_query = AsyncMock(return_value=_vec_normalizado())
    return SQLiteMemoryRepository(str(tmp_path / "mem.db"), embedder_mock)


@pytest.fixture
def mock_embedder_unitario() -> MagicMock:
    """Embedder que devuelve un vector unitario fijo."""
    embedder = MagicMock()
    embedder.embed_query = AsyncMock(return_value=_vec_normalizado())
    return embedder


@pytest.fixture
def knowledge_orchestrator(db_memory: SQLiteMemoryRepository) -> KnowledgeOrchestrator:
    fuente = SqliteMemoryKnowledgeSource(memory=db_memory)
    return KnowledgeOrchestrator(sources=[fuente], max_total_chunks=10)


@pytest.fixture
def use_case(
    agent_config,
    mock_llm,
    db_memory: SQLiteMemoryRepository,
    mock_embedder_unitario,
    mock_skills,
    mock_history,
    mock_tools,
    knowledge_orchestrator: KnowledgeOrchestrator,
) -> RunAgentUseCase:
    return RunAgentUseCase(
        llm=mock_llm,
        memory=db_memory,
        embedder=mock_embedder_unitario,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        agent_config=agent_config,
        knowledge_orchestrator=knowledge_orchestrator,
    )


class TestPreFetchPopulatesKnowledgeChunks:
    """El pre-fetch de knowledge popula AgentContext.knowledge_chunks."""

    async def test_pre_fetch_con_memoria_vacia_no_falla(
        self,
        use_case: RunAgentUseCase,
        mock_llm,
    ) -> None:
        """Con memoria vacía el use case ejecuta sin error."""
        mock_llm.complete.return_value = LLMResponse.of_text("Respuesta sin contexto")

        respuesta = await use_case.execute("¿Qué recuerdas de mí?")
        assert respuesta == "Respuesta sin contexto"

    async def test_pre_fetch_con_memorias_almacenadas(
        self,
        db_memory: SQLiteMemoryRepository,
        use_case: RunAgentUseCase,
        mock_llm,
    ) -> None:
        """Memorias almacenadas en SQLite real son recuperadas y pasan al sistema."""
        # Almacenar una memoria relevante en la DB real
        entrada = MemoryEntry(
            content="El usuario se llama Martín y vive en Buenos Aires.",
            embedding=_vec_normalizado(),
            relevance=0.9,
            tags=["perfil"],
            agent_id="test",
        )
        await db_memory.store(entrada)

        mock_llm.complete.return_value = LLMResponse.of_text("Hola Martín")

        # El pre-fetch ocurre en execute(); capturamos el system_prompt que llega al LLM
        captured_system_prompt: list[str] = []

        async def _capture_complete(messages, system_prompt, tools=None):
            captured_system_prompt.append(system_prompt or "")
            return LLMResponse.of_text("Hola Martín")

        mock_llm.complete = _capture_complete

        await use_case.execute("¿Cómo me llamo?")

        # El system prompt debe contener la sección de knowledge
        assert len(captured_system_prompt) >= 1
        system_prompt = captured_system_prompt[0]

        assert "## Relevant Knowledge" in system_prompt
        assert "Martín" in system_prompt

    async def test_pre_fetch_score_en_rango_valido(
        self,
        db_memory: SQLiteMemoryRepository,
        knowledge_orchestrator: KnowledgeOrchestrator,
        mock_embedder_unitario,
    ) -> None:
        """Los scores del pre-fetch deben estar en [-1, 1]."""
        entrada = MemoryEntry(
            content="Contenido de prueba",
            embedding=_vec_normalizado(),
            relevance=0.5,
            tags=[],
            agent_id="test",
        )
        await db_memory.store(entrada)

        query_vec = _vec_normalizado()
        chunks = await knowledge_orchestrator.retrieve_all(
            query_vec=query_vec,
            top_k=5,
            min_score=0.0,
        )

        assert len(chunks) >= 1
        for chunk in chunks:
            assert -1.0 <= chunk.score <= 1.0

    async def test_sin_orquestador_execute_funciona_normal(
        self,
        agent_config,
        mock_llm,
        mock_memory,
        mock_embedder_unitario,
        mock_skills,
        mock_history,
        mock_tools,
    ) -> None:
        """Con knowledge_orchestrator=None el use case funciona sin error."""
        use_case_sin_knowledge = RunAgentUseCase(
            llm=mock_llm,
            memory=mock_memory,
            embedder=mock_embedder_unitario,
            skills=mock_skills,
            history=mock_history,
            tools=mock_tools,
            agent_config=agent_config,
            knowledge_orchestrator=None,
        )
        mock_llm.complete.return_value = LLMResponse.of_text("OK sin knowledge")

        respuesta = await use_case_sin_knowledge.execute("hola")
        assert respuesta == "OK sin knowledge"


class TestPreFetchBypassOnShortInput:
    """El pre-fetch se saltea con short-input bypass."""

    async def test_bypass_activo_no_llama_orchestrator(
        self,
        mock_llm,
        db_memory,
        mock_embedder_unitario,
        mock_skills,
        mock_history,
        mock_tools,
    ) -> None:
        from core.domain.value_objects.conversation_state import ConversationState
        from infrastructure.config import (
            AgentConfig,
            ChatHistoryConfig,
            EmbeddingConfig,
            LLMConfig,
            MemoryConfig,
            SemanticRoutingConfig,
        )

        # Config con min_words_threshold=5 → input "ok" (1 palabra) activa bypass
        agent_config_bypass = AgentConfig(
            id="test-bypass",
            name="Test Bypass",
            description="Agente de test con bypass",
            system_prompt="Sistema",
            llm=LLMConfig(provider="openrouter", model="test-model"),
            embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
            memory=MemoryConfig(db_filename=":memory:", default_top_k=3),
            chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history_bypass.db"),
            semantic_routing=SemanticRoutingConfig(min_words_threshold=5),
        )

        # Configurar sticky state con selecciones previas para activar bypass
        mock_history.load_state.return_value = ConversationState(
            sticky_skills={"skill-1": 999},
            sticky_tools={"knowledge_search": 999},
        )

        orchestrator_mock = MagicMock()
        orchestrator_mock.retrieve_all = AsyncMock(return_value=[])
        orchestrator_mock.source_ids = []
        orchestrator_mock.token_budget_threshold = 0  # deshabilitar warning en tests

        use_case_bypass = RunAgentUseCase(
            llm=mock_llm,
            memory=db_memory,
            embedder=mock_embedder_unitario,
            skills=mock_skills,
            history=mock_history,
            tools=mock_tools,
            agent_config=agent_config_bypass,
            knowledge_orchestrator=orchestrator_mock,
        )

        mock_llm.complete.return_value = LLMResponse.of_text("ok")
        # Input corto "ok" (1 palabra < 5 threshold) con sticky previo → bypass activo
        await use_case_bypass.execute("ok")

        # El orquestador NO debe ser llamado en bypass
        orchestrator_mock.retrieve_all.assert_not_called()
