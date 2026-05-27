from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from core.domain.entities.message import Message
from core.domain.value_objects.llm_response import LLMResponse


class ILLMProvider(ABC):
    @property
    def thinking_active(self) -> bool:
        """¿Este provider va a producir reasoning_content en el turno actual?

        Default ``False``. Providers con thinking mode (DeepSeek V4 con
        ``reasoning_effort`` set, o-series, etc.) override según su config.
        Lo usa el tool loop para emitir "Thinking..." al canal una vez por turno.
        """
        return False

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        """Stream de chunks de texto. Las implementaciones son async generators
        (``async def`` con ``yield``) que devuelven un ``AsyncIterator[str]``.

        El abstract se declara como ``def`` (no ``async def``) porque sin un
        ``yield`` interno mypy interpretaría ``async def`` como una corrutina
        que devuelve un AsyncIterator — incompatible con los async generators
        de las implementaciones. Ver
        https://mypy.readthedocs.io/en/stable/more_types.html#asynchronous-iterators.
        """
        ...
