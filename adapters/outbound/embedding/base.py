from abc import abstractmethod

from pydantic import BaseModel

from core.ports.outbound.embedding_port import IEmbeddingProvider


class ResolvedEmbeddingConfig(BaseModel):
    """EmbeddingConfig + credenciales resueltas del registry.

    Vive en adapters: es el contrato de entrada que los providers declaran en
    SU capa. La factory de infrastructure lo compone desde la config YAML.
    """

    provider: str
    model_dirname: str
    model: str
    dimension: int
    cache_filename: str
    api_key: str | None = None
    base_url: str | None = None


class BaseEmbeddingProvider(IEmbeddingProvider):
    """Clase base para todos los proveedores de embeddings.

    ``REQUIRES_CREDENTIALS`` indica si la factory debe exigir una entrada en
    ``providers:`` al resolver las creds. Providers locales (e5_onnx) lo
    override a ``False``.
    """

    REQUIRES_CREDENTIALS: bool = True

    def __init__(self, cfg: ResolvedEmbeddingConfig) -> None:
        """Signature común para la factory (`adapter_type(resolved)`).
        Las subclases override este __init__ para su setup específico."""
        self._cfg = cfg

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]: ...

    @abstractmethod
    async def embed_passage(self, text: str) -> list[float]: ...
