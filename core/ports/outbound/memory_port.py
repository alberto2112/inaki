from abc import ABC, abstractmethod
from core.domain.entities.memory import MemoryEntry


class IMemoryRepository(ABC):
    @abstractmethod
    async def store(self, entry: MemoryEntry) -> None: ...

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[MemoryEntry]: ...

    @abstractmethod
    async def search_with_scores(
        self,
        query_vec: list[float],
        top_k: int = 5,
    ) -> list[tuple[MemoryEntry, float]]:
        """
        Busca las memorias más similares y devuelve pares (entrada, score coseno).

        El score se calcula como ``score = 1 - distance² / 2`` donde ``distance``
        es la distancia L2 entre vectores normalizados. Para vectores unitarios
        esto equivale exactamente al coseno ∈ [-1, 1].

        Args:
            query_vec: Vector de embedding de la consulta (dimensión 384).
            top_k: Número máximo de resultados.

        Returns:
            Lista de tuplas (MemoryEntry, score) ordenada por score descendente.
        """
        ...

    @abstractmethod
    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]: ...
