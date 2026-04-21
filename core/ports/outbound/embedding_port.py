from abc import ABC, abstractmethod


class IEmbeddingProvider(ABC):
    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Prefijo 'query:' aplicado internamente por el adaptador."""
        ...

    @abstractmethod
    async def embed_passage(self, text: str) -> list[float]:
        """Prefijo 'passage:' aplicado internamente por el adaptador."""
        ...
