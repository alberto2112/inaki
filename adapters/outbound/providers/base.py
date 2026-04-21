from abc import abstractmethod
from collections.abc import AsyncIterator
from core.domain.entities.message import Message, Role
from core.domain.value_objects.llm_response import LLMResponse
from core.ports.outbound.llm_port import ILLMProvider


class BaseLLMProvider(ILLMProvider):
    """Clase base para todos los proveedores LLM. Define contrato común.

    ``REQUIRES_CREDENTIALS`` indica si la factory debe exigir una entrada en
    ``providers:`` al resolver las creds. Providers locales (ollama) lo
    override a ``False`` para permitir arrancar sin registry.
    """

    REQUIRES_CREDENTIALS: bool = True

    @staticmethod
    def _format_response_log(provider: str, content: str, tool_calls: list[dict]) -> str:
        """Formato unificado del log INFO por cada respuesta del LLM.

        Si hay tool_calls → enumera nombre(args_truncados) de cada call.
        Si no → preview del contenido textual.
        """
        if tool_calls:
            summary = ", ".join(
                f"{tc.get('function', {}).get('name', '?')}"
                f"({str(tc.get('function', {}).get('arguments', ''))[:120]})"
                for tc in tool_calls
            )
            return f"{provider} response: tool_calls=[{summary}]"
        return f"{provider} response: content_preview={content[:200]}"

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
                # OpenAI-compatible: un mismo mensaje assistant puede tener
                # content textual Y tool_calls. Si no hubo texto, pasamos None.
                result.append(
                    {
                        "role": "assistant",
                        "content": m.content if m.content else None,
                        "tool_calls": m.tool_calls,
                    }
                )
            elif m.role == Role.TOOL:
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id or "",
                        "content": m.content,
                    }
                )
            elif m.role in (Role.USER, Role.ASSISTANT):
                result.append({"role": m.role.value, "content": m.content})
        return result

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
