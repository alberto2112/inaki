from abc import abstractmethod
from collections.abc import AsyncIterator
from core.domain.entities.message import Message
from core.ports.outbound.llm_port import ILLMProvider


class BaseLLMProvider(ILLMProvider):
    """Clase base para todos los proveedores LLM. Define contrato común."""

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
