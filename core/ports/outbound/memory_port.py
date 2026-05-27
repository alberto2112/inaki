from typing import Protocol, runtime_checkable

from core.domain.entities.memory import MemoryEntry


@runtime_checkable
class IMemoryRepository(Protocol):
    """Port estructural para repositorios de memoria.

    Declarado como Protocol (no ABC) para permitir duck typing — útil en
    tests con fakes que implementan los métodos sin heredar explícitamente.
    Misma convención que IEmbeddingProvider.
    """

    async def store(self, entry: MemoryEntry) -> None: ...

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[MemoryEntry]: ...

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

    async def get_recent(
        self,
        limit: int = 10,
        agent_id: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[MemoryEntry]:
        """
        Devuelve los `limit` recuerdos más recientes, opcionalmente filtrados por
        ``(agent_id, channel, chat_id)``. Cada filtro es opcional e
        independiente; ``None`` significa "sin filtro por ese campo".

        Las memorias soft-deleted (``deleted=True``) no se incluyen.
        """
        ...

    async def delete(self, memory_id: str) -> MemoryEntry | None:
        """
        Soft-delete por id. La entry deja de aparecer en ``search`` y
        ``get_recent`` pero permanece en almacenamiento (reversible).

        Devuelve la entry borrada (con ``deleted=True``) o ``None`` si el id
        no existía o ya estaba borrado (idempotencia).
        """
        ...

    async def update(
        self,
        memory_id: str,
        content: str | None = None,
        tags: list[str] | None = None,
        relevance: float | None = None,
        embedding: list[float] | None = None,
    ) -> MemoryEntry | None:
        """
        Update parcial. Solo se actualizan los campos no-``None``.

        Si se cambia ``content``, el caller debería pasar también ``embedding``
        recomputado — el repo NO recalcula embeddings automáticamente porque
        no tiene la dependencia del ``IEmbeddingProvider``.

        Devuelve la entry actualizada o ``None`` si el id no existe o está
        soft-deleted (no se permite editar un recuerdo borrado).
        """
        ...
