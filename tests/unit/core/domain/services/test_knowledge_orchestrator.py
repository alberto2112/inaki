"""Tests unitarios para KnowledgeOrchestrator."""

from __future__ import annotations

import pytest

from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator
from core.domain.value_objects.knowledge_chunk import KnowledgeChunk
from core.ports.outbound.knowledge_port import IKnowledgeSource


# ---------------------------------------------------------------------------
# Helpers — fuentes mock
# ---------------------------------------------------------------------------


def _chunk(source_id: str, score: float, content: str = "texto") -> KnowledgeChunk:
    return KnowledgeChunk(source_id=source_id, content=content, score=score)


class _StaticSource(IKnowledgeSource):
    """Fuente que devuelve una lista fija de chunks."""

    def __init__(self, sid: str, chunks: list[KnowledgeChunk]) -> None:
        self._sid = sid
        self._chunks = chunks

    @property
    def source_id(self) -> str:
        return self._sid

    @property
    def description(self) -> str:
        return f"Fuente estática '{self._sid}'"

    async def search(
        self, query_vec: list[float], top_k: int, min_score: float
    ) -> list[KnowledgeChunk]:
        return self._chunks


class _FailingSource(IKnowledgeSource):
    """Fuente que siempre lanza una excepción."""

    def __init__(self, sid: str = "falla") -> None:
        self._sid = sid

    @property
    def source_id(self) -> str:
        return self._sid

    @property
    def description(self) -> str:
        return "Fuente que falla"

    async def search(
        self, query_vec: list[float], top_k: int, min_score: float
    ) -> list[KnowledgeChunk]:
        raise RuntimeError("conexión perdida")


_VEC = [0.1] * 384


# ---------------------------------------------------------------------------
# Ordenamiento por score descendente
# ---------------------------------------------------------------------------


async def test_chunks_sorted_by_score_descending() -> None:
    fuente = _StaticSource(
        "mem",
        [_chunk("mem", 0.3), _chunk("mem", 0.9), _chunk("mem", 0.6)],
    )
    orq = KnowledgeOrchestrator(sources=[fuente], max_total_chunks=10)
    resultado = await orq.retrieve_all(_VEC, top_k=5, min_score=0.0)

    scores = [c.score for c in resultado]
    assert scores == sorted(scores, reverse=True)


async def test_chunks_from_multiple_sources_sorted_together() -> None:
    a = _StaticSource("a", [_chunk("a", 0.8), _chunk("a", 0.4)])
    b = _StaticSource("b", [_chunk("b", 0.95), _chunk("b", 0.2)])
    orq = KnowledgeOrchestrator(sources=[a, b], max_total_chunks=10)
    resultado = await orq.retrieve_all(_VEC, top_k=5, min_score=0.0)

    scores = [c.score for c in resultado]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# Cap de max_total_chunks
# ---------------------------------------------------------------------------


async def test_cap_limits_total_chunks() -> None:
    fuente = _StaticSource(
        "mem",
        [_chunk("mem", float(i) / 10) for i in range(10)],  # 10 chunks
    )
    orq = KnowledgeOrchestrator(sources=[fuente], max_total_chunks=3)
    resultado = await orq.retrieve_all(_VEC, top_k=10, min_score=0.0)

    assert len(resultado) == 3


async def test_cap_keeps_highest_scores() -> None:
    fuente = _StaticSource(
        "mem",
        [_chunk("mem", 0.1), _chunk("mem", 0.9), _chunk("mem", 0.5)],
    )
    orq = KnowledgeOrchestrator(sources=[fuente], max_total_chunks=2)
    resultado = await orq.retrieve_all(_VEC, top_k=5, min_score=0.0)

    assert len(resultado) == 2
    assert resultado[0].score == pytest.approx(0.9)
    assert resultado[1].score == pytest.approx(0.5)


async def test_cap_larger_than_total_returns_all() -> None:
    fuente = _StaticSource("mem", [_chunk("mem", 0.5), _chunk("mem", 0.7)])
    orq = KnowledgeOrchestrator(sources=[fuente], max_total_chunks=100)
    resultado = await orq.retrieve_all(_VEC, top_k=5, min_score=0.0)

    assert len(resultado) == 2


# ---------------------------------------------------------------------------
# Aislamiento de fallos por fuente
# ---------------------------------------------------------------------------


async def test_failing_source_does_not_crash_orchestrator() -> None:
    buena = _StaticSource("buena", [_chunk("buena", 0.8)])
    falla = _FailingSource("mala")
    orq = KnowledgeOrchestrator(sources=[buena, falla], max_total_chunks=10)

    resultado = await orq.retrieve_all(_VEC, top_k=5, min_score=0.0)

    # La fuente buena sigue devolviendo su chunk
    assert len(resultado) == 1
    assert resultado[0].source_id == "buena"


async def test_all_failing_sources_returns_empty() -> None:
    orq = KnowledgeOrchestrator(
        sources=[_FailingSource("a"), _FailingSource("b")],
        max_total_chunks=10,
    )
    resultado = await orq.retrieve_all(_VEC, top_k=5, min_score=0.0)

    assert resultado == []


async def test_partial_failure_isolates_bad_source() -> None:
    a = _StaticSource("a", [_chunk("a", 0.9)])
    b = _FailingSource("b")
    c = _StaticSource("c", [_chunk("c", 0.5)])
    orq = KnowledgeOrchestrator(sources=[a, b, c], max_total_chunks=10)

    resultado = await orq.retrieve_all(_VEC, top_k=5, min_score=0.0)

    ids = {r.source_id for r in resultado}
    assert "a" in ids
    assert "c" in ids
    assert "b" not in ids


# ---------------------------------------------------------------------------
# Sin fuentes
# ---------------------------------------------------------------------------


async def test_no_sources_returns_empty() -> None:
    orq = KnowledgeOrchestrator(sources=[], max_total_chunks=10)
    resultado = await orq.retrieve_all(_VEC, top_k=5, min_score=0.0)
    assert resultado == []


# ---------------------------------------------------------------------------
# Cap = 0
# ---------------------------------------------------------------------------


async def test_cap_zero_returns_empty() -> None:
    fuente = _StaticSource("mem", [_chunk("mem", 0.8)])
    orq = KnowledgeOrchestrator(sources=[fuente], max_total_chunks=0)
    resultado = await orq.retrieve_all(_VEC, top_k=5, min_score=0.0)
    assert resultado == []
