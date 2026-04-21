from abc import abstractmethod
from core.ports.outbound.embedding_port import IEmbeddingProvider


class BaseEmbeddingProvider(IEmbeddingProvider):
    """Clase base para todos los proveedores de embeddings.

    ``REQUIRES_CREDENTIALS`` indica si la factory debe exigir una entrada en
    ``providers:`` al resolver las creds. Providers locales (e5_onnx) lo
    override a ``False``.
    """

    REQUIRES_CREDENTIALS: bool = True

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]: ...

    @abstractmethod
    async def embed_passage(self, text: str) -> list[float]: ...
