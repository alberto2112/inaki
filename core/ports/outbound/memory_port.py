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
    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]: ...
