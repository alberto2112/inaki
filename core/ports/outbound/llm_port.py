from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from core.domain.entities.message import Message
from core.domain.value_objects.llm_response import LLMResponse


class ILLMProvider(ABC):

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]: ...
