from abc import ABC, abstractmethod


class IAgentUseCase(ABC):

    @abstractmethod
    async def execute(self, agent_id: str, user_input: str) -> str: ...
