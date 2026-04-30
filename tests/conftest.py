"""Fixtures compartidas para todos los tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.domain.entities.face import (
    BBox,
    FaceDetection,
    FaceMatch,
    MatchStatus,
    MessageFaceMetadata,
    Person,
)
from core.domain.value_objects.conversation_state import ConversationState
from core.domain.value_objects.llm_response import LLMResponse
from infrastructure.config import (
    AgentConfig,
    ChatHistoryConfig,
    EmbeddingConfig,
    LLMConfig,
    MemoryConfig,
    ProviderConfig,
)


def _build_providers() -> dict[str, ProviderConfig]:
    """Registro mínimo de providers compartido por fixtures de test."""
    return {
        "openrouter": ProviderConfig(api_key="test-key"),
        "openai": ProviderConfig(api_key="test-openai"),
        "groq": ProviderConfig(api_key="test-groq"),
        "e5_onnx": ProviderConfig(),
        "ollama": ProviderConfig(),
    }


@pytest.fixture
def agent_config() -> AgentConfig:
    return AgentConfig(
        id="test",
        name="Test Agent",
        description="Agente de test",
        system_prompt="Eres un asistente de test.",
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memory=MemoryConfig(db_filename=":memory:", default_top_k=3),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
        providers=_build_providers(),
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
    memory.search_with_scores.return_value = []
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
    history.load_state.return_value = ConversationState()
    history.save_state.return_value = None
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


@pytest.fixture
def mock_transcription() -> AsyncMock:
    """Mock de ITranscriptionProvider — devuelve texto fake por defecto."""
    transcription = AsyncMock()
    transcription.transcribe.return_value = "transcripción de prueba"
    return transcription


# ---------------------------------------------------------------------------
# Fixtures de reconocimiento facial (Phase 1.4)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_face_detection() -> FaceDetection:
    """FaceDetection de ejemplo con un embedding de 512 floats."""
    return FaceDetection(
        bbox=BBox(x=10, y=20, w=100, h=150),
        embedding=[0.1] * 512,
        detection_score=0.95,
    )


@pytest.fixture
def sample_person() -> Person:
    """Persona de ejemplo (conocida, sin categoría especial)."""
    return Person(
        nombre="Alberto",
        apellido="García",
        relacion="dueño",
        embeddings_count=3,
    )


@pytest.fixture
def sample_ignored_person() -> Person:
    """Persona ignorada (registrada via skip_face). nombre=None, categoria='ignorada'."""
    return Person(
        nombre=None,
        categoria="ignorada",
        embeddings_count=1,
    )


@pytest.fixture
def mock_vision() -> AsyncMock:
    """Mock de IVisionPort. Por defecto devuelve una sola FaceDetection."""
    vision = AsyncMock()
    vision.detect_and_embed.return_value = [
        FaceDetection(
            bbox=BBox(x=10, y=20, w=100, h=150),
            embedding=[0.1] * 512,
            detection_score=0.95,
        )
    ]
    return vision


@pytest.fixture
def mock_face_registry() -> AsyncMock:
    """Mock de IFaceRegistryPort. Por defecto find_matches devuelve lista vacía."""
    registry = AsyncMock()
    registry.find_matches.return_value = []
    registry.list_persons.return_value = []
    registry.get_person.return_value = None
    registry.get_centroid.return_value = None
    return registry


@pytest.fixture
def mock_scene_describer() -> AsyncMock:
    """Mock de ISceneDescriberPort. Por defecto devuelve descripción genérica."""
    describer = AsyncMock()
    describer.describe_image.return_value = "Dos personas en un café tomando mate."
    return describer


@pytest.fixture
def mock_annotator() -> MagicMock:
    """Mock del PillowAnnotator. Por defecto devuelve bytes fake."""
    annotator = MagicMock()
    annotator.draw_numbered.return_value = b"\xff\xd8\xff"  # JPEG magic bytes fake
    return annotator


@pytest.fixture
def mock_metadata_repo() -> AsyncMock:
    """Mock de IMessageFaceMetadataRepo."""
    repo = AsyncMock()
    repo.save.return_value = None
    repo.get_by_history_id.return_value = None
    repo.find_recent_for_thread.return_value = []
    repo.resolve_face_ref.return_value = None
    return repo
