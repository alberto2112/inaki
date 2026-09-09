"""
SqliteMemoryKnowledgeSource — adapta IMemoryRepository como fuente de conocimiento.

Wraps search_with_scores() de IMemoryRepository y expone IKnowledgeSource.
Aplica un umbral mínimo de score (min_score) para descartar resultados poco relevantes.
"""

from __future__ import annotations

import logging

from core.domain.value_objects.knowledge_chunk import KnowledgeChunk
from core.ports.outbound.knowledge_port import IKnowledgeSource
from core.ports.outbound.memory_port import IMemoryRepository

logger = logging.getLogger(__name__)


class SqliteMemoryKnowledgeSource(IKnowledgeSource):
    """Fuente de conocimiento que consulta la memoria SQLite del agente."""

    def __init__(self, memory: IMemoryRepository) -> None:
        self._memory = memory

    @property
    def source_id(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "Memorias a largo plazo del agente (SQLite + sqlite-vec)"

    async def search(
        self,
        query_vec: list[float],
        top_k: int,
        min_score: float,
    ) -> list[KnowledgeChunk]:
        """
        Busca en la memoria del agente y devuelve KnowledgeChunks con score ≥ min_score.

        Args:
            query_vec: Vector de embedding de la consulta.
            top_k: Número máximo de resultados a solicitar al repositorio.
            min_score: Umbral mínimo de score coseno. Los resultados por debajo
                de este umbral se descartan (se aplica max(0, score) antes de comparar).

        Returns:
            Lista de KnowledgeChunk filtrada por min_score, ordenada por score desc.
        """
        pares = await self._memory.search_with_scores(query_vec, top_k=top_k)

        fragmentos: list[KnowledgeChunk] = []
        for entrada, score in pares:
            # Clamp negatives a 0 para la comparación con min_score
            score_efectivo = max(0.0, score)
            if score_efectivo < min_score:
                logger.debug(
                    "SqliteMemoryKnowledgeSource: descartando fragmento (score=%.4f < min_score=%.4f) id=%s",
                    score,
                    min_score,
                    entrada.id,
                )
                continue

            fragmentos.append(
                KnowledgeChunk(
                    source_id=self.source_id,
                    content=entrada.content,
                    score=score,
                    metadata={
                        "id": entrada.id,
                        "relevance": entrada.relevance,
                        "tags": entrada.tags,
                        "created_at": entrada.created_at.isoformat(),
                    },
                )
            )

        return fragmentos
