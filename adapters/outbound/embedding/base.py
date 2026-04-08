from abc import abstractmethod
from core.ports.outbound.embedding_port import IEmbeddingProvider


class BaseEmbeddingProvider(IEmbeddingProvider):
    """Clase base para todos los proveedores de embeddings."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]: ...

    @abstractmethod
    async def embed_passage(self, text: str) -> list[float]: ...
