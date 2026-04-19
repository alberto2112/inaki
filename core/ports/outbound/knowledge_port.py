"""
Puerto de salida para fuentes de conocimiento.

Las implementaciones concretas viven en adapters/outbound/knowledge/.
Este puerto es de solo lectura — NO hereda de IMemoryRepository (que es
lectura+escritura) para respetar el principio de sustitución de Liskov.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.domain.value_objects.knowledge_chunk import KnowledgeChunk


class IKnowledgeSource(ABC):
    """Interfaz de una fuente de conocimiento consultable por vector de embedding."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Identificador único de esta fuente (ej. 'memory', 'docs-proyecto')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Descripción breve de qué contiene esta fuente. Usada en logs y debug."""
        ...

    @abstractmethod
    async def search(
        self,
        query_vec: list[float],
        top_k: int,
        min_score: float,
    ) -> list[KnowledgeChunk]:
        """
        Busca los fragmentos más relevantes para el vector de consulta.

        Args:
            query_vec: Vector de embedding de la consulta (dimensión 384).
            top_k: Número máximo de fragmentos a retornar.
            min_score: Score mínimo de similitud coseno (inclusive). Los fragmentos
                con score < min_score se descartan. Rango típico: [0.0, 1.0].

        Returns:
            Lista de KnowledgeChunk ordenada por score descendente.
            Puede retornar menos de top_k si no hay fragmentos suficientes.
        """
        ...
