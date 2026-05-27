from typing import Protocol, runtime_checkable


@runtime_checkable
class IEmbeddingProvider(Protocol):
    """Port estructural para providers de embeddings.

    Declarado como Protocol (no ABC) para permitir duck typing — útil en
    tests con fakes que implementan los métodos sin heredar explícitamente.
    Las implementaciones reales (BaseEmbeddingProvider y sus subclases)
    pueden seguir heredando explícitamente para documentar la intención.

    @runtime_checkable permite isinstance(x, IEmbeddingProvider) en runtime.
    """

    async def embed_query(self, text: str) -> list[float]:
        """Prefijo 'query:' aplicado internamente por el adaptador."""
        ...

    async def embed_passage(self, text: str) -> list[float]:
        """Prefijo 'passage:' aplicado internamente por el adaptador."""
        ...
