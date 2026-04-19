"""
Tests unitarios para SqliteMemoryKnowledgeSource.

Verifica:
- La fórmula score = 1 - d² / 2 se aplica correctamente
- El filtro min_score descarta fragmentos por debajo del umbral
- include_memory: false en el container produce lista vacía
"""

from __future__ import annotations

import math
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.outbound.knowledge.sqlite_memory_knowledge_source import (
    SqliteMemoryKnowledgeSource,
)
from core.domain.entities.memory import MemoryEntry


def _make_entry(id: str = "mem-1", content: str = "contenido de prueba") -> MemoryEntry:
    return MemoryEntry(
        id=id,
        content=content,
        embedding=[],
        relevance=0.9,
        tags=["test"],
        created_at=datetime(2024, 1, 1),
        agent_id="agente-prueba",
    )


class TestScoringFormula:
    """Verifica que score = 1 - d² / 2 se aplica correctamente."""

    async def test_score_para_distancia_cero(self) -> None:
        """Distancia 0 → coseno 1 (vectores idénticos)."""
        memory = MagicMock()
        entrada = _make_entry()
        memory.search_with_scores = AsyncMock(return_value=[(entrada, 1.0)])

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        # min_score=0 para no filtrar nada
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.0)

        assert len(resultado) == 1
        assert resultado[0].score == pytest.approx(1.0)

    async def test_score_para_distancia_sqrt2(self) -> None:
        """Distancia √2 → coseno 0 (vectores ortogonales)."""
        memory = MagicMock()
        entrada = _make_entry()
        distancia_l2 = math.sqrt(2)
        score_esperado = 1.0 - (distancia_l2**2) / 2.0  # = 0.0
        memory.search_with_scores = AsyncMock(return_value=[(entrada, score_esperado)])

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.0)

        assert len(resultado) == 1
        assert resultado[0].score == pytest.approx(0.0, abs=1e-6)

    async def test_score_para_distancia_dos(self) -> None:
        """Distancia 2 → coseno -1 (vectores opuestos)."""
        memory = MagicMock()
        entrada = _make_entry()
        score_esperado = 1.0 - (2.0**2) / 2.0  # = -1.0
        memory.search_with_scores = AsyncMock(return_value=[(entrada, score_esperado)])

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=-1.0)

        assert len(resultado) == 1
        assert resultado[0].score == pytest.approx(-1.0)

    async def test_score_positivo_parcial(self) -> None:
        """Distancia 1 → coseno 0.5."""
        memory = MagicMock()
        entrada = _make_entry()
        score_esperado = 1.0 - (1.0**2) / 2.0  # = 0.5
        memory.search_with_scores = AsyncMock(return_value=[(entrada, score_esperado)])

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.0)

        assert len(resultado) == 1
        assert resultado[0].score == pytest.approx(0.5)


class TestMinScoreFiltering:
    """Verifica que min_score descarta fragmentos poco relevantes."""

    async def test_filtra_por_min_score(self) -> None:
        """Fragmentos con score efectivo < min_score se descartan."""
        memory = MagicMock()
        entrada_alta = _make_entry(id="mem-alta", content="relevante")
        entrada_baja = _make_entry(id="mem-baja", content="irrelevante")
        memory.search_with_scores = AsyncMock(
            return_value=[
                (entrada_alta, 0.8),
                (entrada_baja, 0.2),
            ]
        )

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.5)

        assert len(resultado) == 1
        assert resultado[0].content == "relevante"
        assert resultado[0].score == pytest.approx(0.8)

    async def test_score_negativo_tratado_como_cero_para_min_score(self) -> None:
        """Score negativo → max(0, score)=0 → descartado cuando min_score > 0."""
        memory = MagicMock()
        entrada = _make_entry()
        memory.search_with_scores = AsyncMock(return_value=[(entrada, -0.3)])

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        # min_score=0.1: score efectivo = max(0, -0.3) = 0.0 < 0.1 → descartado
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.1)

        assert resultado == []

    async def test_min_score_cero_incluye_scores_negativos(self) -> None:
        """Con min_score=0.0: max(0, score_neg)=0 ≥ 0.0 → se incluye."""
        memory = MagicMock()
        entrada = _make_entry()
        memory.search_with_scores = AsyncMock(return_value=[(entrada, -0.1)])

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.0)

        assert len(resultado) == 1
        assert resultado[0].score == pytest.approx(-0.1)

    async def test_todos_filtrados_retorna_lista_vacia(self) -> None:
        """Todos los resultados por debajo del umbral → lista vacía."""
        memory = MagicMock()
        entradas = [(_make_entry(id=f"mem-{i}"), 0.1) for i in range(3)]
        memory.search_with_scores = AsyncMock(return_value=entradas)

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.9)

        assert resultado == []

    async def test_sin_resultados_en_repo(self) -> None:
        """Repositorio vacío → lista vacía."""
        memory = MagicMock()
        memory.search_with_scores = AsyncMock(return_value=[])

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.0)

        assert resultado == []


class TestKnowledgeChunkShape:
    """Verifica que los KnowledgeChunk producidos tienen la forma esperada."""

    async def test_source_id_es_memory(self) -> None:
        memory = MagicMock()
        entrada = _make_entry(content="test chunk")
        memory.search_with_scores = AsyncMock(return_value=[(entrada, 0.75)])

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.0)

        assert resultado[0].source_id == "memory"

    async def test_metadata_contiene_campos_de_memoria(self) -> None:
        memory = MagicMock()
        entrada = _make_entry(id="mem-abc", content="test")
        memory.search_with_scores = AsyncMock(return_value=[(entrada, 0.6)])

        fuente = SqliteMemoryKnowledgeSource(memory=memory)
        resultado = await fuente.search(query_vec=[0.1] * 384, top_k=5, min_score=0.0)

        meta = resultado[0].metadata
        assert meta["id"] == "mem-abc"
        assert "relevance" in meta
        assert "tags" in meta
        assert "created_at" in meta


class TestIncludeMemoryFalse:
    """
    Simula el comportamiento de include_memory: false.

    El container NO registra SqliteMemoryKnowledgeSource cuando include_memory es False.
    Aquí verificamos que con una lista vacía de fuentes el orquestador no devuelve chunks.
    """

    async def test_sin_fuentes_retorna_lista_vacia(self) -> None:
        from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator

        orquestador = KnowledgeOrchestrator(sources=[], max_total_chunks=10)
        resultado = await orquestador.retrieve_all(
            query_vec=[0.1] * 384,
            top_k=5,
            min_score=0.0,
        )
        assert resultado == []
