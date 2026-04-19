"""
KnowledgeOrchestrator — fan-out a N fuentes de conocimiento en paralelo.

Características:
  - Consulta todas las fuentes en paralelo via asyncio.gather.
  - Aísla fallos por fuente: si una fuente falla, las demás siguen.
  - Aplica un cap total (max_total_chunks) después de ordenar por score desc.
  - No tiene dependencias fuera de core/ (sin imports de adapters ni infrastructure).
"""

from __future__ import annotations

import asyncio
import logging

from core.domain.value_objects.knowledge_chunk import KnowledgeChunk
from core.ports.outbound.knowledge_port import IKnowledgeSource

logger = logging.getLogger(__name__)


class KnowledgeOrchestrator:
    """Orquesta la recuperación paralela de fragmentos desde múltiples fuentes."""

    def __init__(
        self,
        sources: list[IKnowledgeSource],
        max_total_chunks: int = 10,
    ) -> None:
        self._fuentes = sources
        self._cap = max_total_chunks

    async def retrieve_all(
        self,
        query_vec: list[float],
        top_k: int,
        min_score: float,
    ) -> list[KnowledgeChunk]:
        """
        Consulta todas las fuentes en paralelo y devuelve los mejores fragmentos.

        El resultado está ordenado por score descendente y limitado a
        max_total_chunks fragmentos en total.

        Las fuentes que fallan emiten un WARNING en structlog y se ignoran
        (fail-isolation); el resto de fuentes sigue procesándose normalmente.

        Args:
            query_vec: Vector de embedding de la consulta.
            top_k: Fragmentos máximos por fuente.
            min_score: Score mínimo de similitud coseno.

        Returns:
            Lista de KnowledgeChunk ordenada por score desc, con cap aplicado.
        """

        async def _consultar_fuente(fuente: IKnowledgeSource) -> list[KnowledgeChunk]:
            try:
                return await fuente.search(query_vec, top_k, min_score)
            except Exception as exc:
                logger.warning(
                    "KnowledgeSource '%s' falló durante la consulta: %s",
                    fuente.source_id,
                    exc,
                )
                return []

        resultados = await asyncio.gather(*(_consultar_fuente(fuente) for fuente in self._fuentes))

        # Aplanar, ordenar por score desc, aplicar cap
        fragmentos: list[KnowledgeChunk] = [chunk for grupo in resultados for chunk in grupo]
        fragmentos.sort(key=lambda c: c.score, reverse=True)
        return fragmentos[: self._cap]
