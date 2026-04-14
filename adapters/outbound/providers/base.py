from abc import abstractmethod
from collections.abc import AsyncIterator
from core.domain.entities.message import Message, Role
from core.ports.outbound.llm_port import ILLMProvider


class BaseLLMProvider(ILLMProvider):
    """Clase base para todos los proveedores LLM. Define contrato común."""

    @staticmethod
    def _build_messages(messages: list[Message], system_prompt: str) -> list[dict]:
        """Construye la lista de mensajes para el API del LLM.

        Maneja correctamente los roles del protocolo de tool calls:
        - ASSISTANT con tool_calls → {"role": "assistant", "tool_calls": [...]}
        - TOOL → {"role": "tool", "tool_call_id": "...", "content": "..."}
        - USER / ASSISTANT (texto) → {"role": "...", "content": "..."}
        """
        result: list[dict] = [{"role": "system", "content": system_prompt}]
        for m in messages:
            if m.role == Role.ASSISTANT and m.tool_calls:
                result.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": m.tool_calls,
                })
            elif m.role == Role.TOOL:
                result.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id or "",
                    "content": m.content,
                })
            elif m.role in (Role.USER, Role.ASSISTANT):
                result.append({"role": m.role.value, "content": m.content})
        return result

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
