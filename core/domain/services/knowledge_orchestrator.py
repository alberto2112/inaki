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
        token_budget_threshold: int = 4000,
        pre_fetch_enabled: bool = True,
        default_top_k_per_source: int = 3,
        default_min_score: float = 0.5,
    ) -> None:
        self._fuentes = sources
        self._cap = max_total_chunks
        # Parámetros almacenados aquí para que RunAgentUseCase los lea sin necesitar
        # GlobalConfig (mantiene core/ desacoplado de infrastructure/).
        self._token_budget_threshold = token_budget_threshold
        self._pre_fetch_enabled = pre_fetch_enabled
        self._default_top_k_per_source = default_top_k_per_source
        self._default_min_score = default_min_score

    @property
    def source_ids(self) -> list[str]:
        """IDs de las fuentes registradas, en orden de registro."""
        return [fuente.source_id for fuente in self._fuentes]

    @property
    def token_budget_threshold(self) -> int:
        """Umbral de advertencia de tokens (0 = deshabilitado)."""
        return self._token_budget_threshold

    @property
    def pre_fetch_enabled(self) -> bool:
        """Si False, el pre-fetch automático por turno se saltea."""
        return self._pre_fetch_enabled

    @property
    def default_top_k_per_source(self) -> int:
        """top_k por fuente usado en el pre-fetch por turno."""
        return self._default_top_k_per_source

    @property
    def default_min_score(self) -> float:
        """min_score usado en el pre-fetch por turno."""
        return self._default_min_score

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
