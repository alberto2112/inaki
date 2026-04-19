"""Tests unitarios para KnowledgeChunk."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.domain.value_objects.knowledge_chunk import KnowledgeChunk


# ---------------------------------------------------------------------------
# Score — validación de rango coseno [-1, 1]
# ---------------------------------------------------------------------------


def test_score_in_valid_range_positive() -> None:
    chunk = KnowledgeChunk(source_id="memory", content="hola", score=0.85)
    assert chunk.score == 0.85


def test_score_in_valid_range_negative() -> None:
    chunk = KnowledgeChunk(source_id="memory", content="hola", score=-0.5)
    assert chunk.score == -0.5


def test_score_at_upper_bound() -> None:
    chunk = KnowledgeChunk(source_id="docs", content="texto", score=1.0)
    assert chunk.score == 1.0


def test_score_at_lower_bound() -> None:
    chunk = KnowledgeChunk(source_id="docs", content="texto", score=-1.0)
    assert chunk.score == -1.0


def test_score_at_zero() -> None:
    chunk = KnowledgeChunk(source_id="docs", content="texto", score=0.0)
    assert chunk.score == 0.0


def test_score_above_upper_bound_raises() -> None:
    with pytest.raises(ValidationError):
        KnowledgeChunk(source_id="docs", content="texto", score=1.01)


def test_score_below_lower_bound_raises() -> None:
    with pytest.raises(ValidationError):
        KnowledgeChunk(source_id="docs", content="texto", score=-1.01)


# ---------------------------------------------------------------------------
# Metadata — default vacío, no se comparte entre instancias
# ---------------------------------------------------------------------------


def test_metadata_default_is_empty_dict() -> None:
    chunk = KnowledgeChunk(source_id="mem", content="hola", score=0.5)
    assert chunk.metadata == {}


def test_metadata_accepts_arbitrary_keys() -> None:
    meta = {"file_path": "/docs/guia.md", "chunk_idx": 3, "created_at": "2026-04-19"}
    chunk = KnowledgeChunk(source_id="docs", content="parrafo", score=0.7, metadata=meta)
    assert chunk.metadata["file_path"] == "/docs/guia.md"
    assert chunk.metadata["chunk_idx"] == 3


def test_metadata_default_not_shared_between_instances() -> None:
    """El default_factory garantiza que cada instancia tiene su propio dict."""
    a = KnowledgeChunk(source_id="x", content="a", score=0.1)
    b = KnowledgeChunk(source_id="y", content="b", score=0.2)
    a.metadata["key"] = "val"
    assert "key" not in b.metadata


# ---------------------------------------------------------------------------
# Campos obligatorios
# ---------------------------------------------------------------------------


def test_missing_source_id_raises() -> None:
    with pytest.raises(ValidationError):
        KnowledgeChunk(content="texto", score=0.5)  # type: ignore[call-arg]


def test_missing_content_raises() -> None:
    with pytest.raises(ValidationError):
        KnowledgeChunk(source_id="mem", score=0.5)  # type: ignore[call-arg]


def test_missing_score_raises() -> None:
    with pytest.raises(ValidationError):
        KnowledgeChunk(source_id="mem", content="texto")  # type: ignore[call-arg]
