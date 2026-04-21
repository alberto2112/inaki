from abc import ABC, abstractmethod
from core.domain.entities.skill import Skill


class ISkillRepository(ABC):
    @abstractmethod
    async def list_all(self) -> list[Skill]: ...

    @abstractmethod
    async def retrieve(
        self,
        query_embedding: list[float],
        top_k: int = 3,
        min_score: float = 0.0,
    ) -> list[Skill]: ...

    @abstractmethod
    async def retrieve_with_scores(
        self,
        query_embedding: list[float],
        top_k: int = 3,
        min_score: float = 0.0,
    ) -> list[tuple[Skill, float]]:
        """Igual que retrieve pero devuelve pares (skill, similitud coseno) ordenados por score."""
        ...
