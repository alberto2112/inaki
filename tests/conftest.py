"""Fixtures compartidas para todos los tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.domain.entities.message import Message, Role
from infrastructure.config import (
    AgentConfig,
    LLMConfig,
    EmbeddingConfig,
    MemoryConfig,
    HistoryConfig,
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
        history=HistoryConfig(active_dir="/tmp/inaki_test/active", archive_dir="/tmp/inaki_test/archive"),
    )


@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.complete.return_value = "Respuesta de test"
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
    return skills


@pytest.fixture
def mock_history() -> AsyncMock:
    history = AsyncMock()
    history.load.return_value = []
    history.append.return_value = None
    history.archive.return_value = "/tmp/archive/test_20240101.txt"
    history.clear.return_value = None
    return history


@pytest.fixture
def mock_tools() -> MagicMock:
    tools = MagicMock()
    tools.get_schemas.return_value = []
    return tools
