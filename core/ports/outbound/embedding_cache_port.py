"""Puerto de caché de embeddings."""

from abc import ABC, abstractmethod


class IEmbeddingCache(ABC):
    @abstractmethod
    async def get(self, content_hash: str, provider: str, dimension: int) -> list[float] | None:
        """Retorna el embedding cacheado o None si no existe."""
        ...

    @abstractmethod
    async def put(
        self, content_hash: str, provider: str, dimension: int, embedding: list[float]
    ) -> None:
        """Almacena un embedding en la caché."""
        ...
