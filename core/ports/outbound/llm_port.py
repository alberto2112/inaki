from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from core.domain.entities.message import Message


class ILLMProvider(ABC):

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> str: ...

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]: ...
