"""Fixtures compartidas para todos los tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.domain.entities.message import Message, Role
from core.domain.value_objects.llm_response import LLMResponse
from infrastructure.config import (
    AgentConfig,
    LLMConfig,
    EmbeddingConfig,
    MemoryConfig,
    ChatHistoryConfig,
)


@pytest.fixture
def agent_config() -> AgentConfig:
    return AgentConfig(
        id="test",
        name="Test Agent",
        description="Agente de test",
        system_prompt="Eres un asistente de test.",
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_path="models/test"),
        memory=MemoryConfig(db_path=":memory:", default_top_k=3),
        chat_history=ChatHistoryConfig(db_path="/tmp/inaki_test/history.db"),
    )


@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse.of_text("Respuesta de test")
    return llm


@pytest.fixture
def mock_memory() -> AsyncMock:
    memory = AsyncMock()
    memory.search.return_value = []
    memory.store.return_value = None
    return memory


@pytest.fixture
def mock_embedder() -> AsyncMock:
    embedder = AsyncMock()
    embedder.embed_query.return_value = [0.1] * 384
    embedder.embed_passage.return_value = [0.1] * 384
    return embedder


@pytest.fixture
def mock_skills() -> AsyncMock:
    skills = AsyncMock()
    skills.retrieve.return_value = []
    skills.retrieve_with_scores.return_value = []
    return skills


@pytest.fixture
def mock_history() -> AsyncMock:
    history = AsyncMock()
    history.load.return_value = []
    history.load_full.return_value = []
    history.load_uninfused.return_value = []
    history.append.return_value = None
    history.mark_infused.return_value = 0
    history.trim.return_value = None
    history.clear.return_value = None
    return history


@pytest.fixture
def mock_tools() -> MagicMock:
    tools = MagicMock()
    tools.get_schemas.return_value = []
    tools.get_schemas_relevant = AsyncMock(return_value=[])
    tools.get_schemas_relevant_with_scores = AsyncMock(return_value=[])
    return tools


@pytest.fixture
def mock_embedding_cache() -> AsyncMock:
    cache = AsyncMock()
    cache.get.return_value = None
    cache.put.return_value = None
    return cache
