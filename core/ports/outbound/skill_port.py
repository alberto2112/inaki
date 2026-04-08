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
    ) -> list[Skill]: ...
