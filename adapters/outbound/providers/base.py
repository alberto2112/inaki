from abc import abstractmethod
from collections.abc import AsyncIterator

from pydantic import BaseModel

from core.domain.entities.message import Message, Role
from core.domain.value_objects.llm_response import LLMResponse
from core.ports.outbound.llm_port import ILLMProvider


class ResolvedLLMConfig(BaseModel):
    """LLMConfig + credenciales del registry resueltas. Lo recibe el adapter.

    Vive en adapters (no en ``infrastructure/config.py``): es el contrato de
    entrada que los providers declaran en SU capa. Las factories de
    infrastructure lo componen desde la config YAML — dirección legal
    (``infrastructure → adapters``); al revés jamás.
    """

    provider: str
    model: str
    temperature: float
    max_tokens: int
    reasoning_effort: str | None = None
    timeout_seconds: int = 60
    api_key: str | None = None
    base_url: str | None = None

    @property
    def thinking_active(self) -> bool:
        """¿Hay que activar thinking mode en este turno?

        Reglas:
          - ``None`` o cadena vacía → desactivado (default).
          - ``"low"`` → desactivado. DeepSeek mapea internamente ``low → high``,
            así que "low" no aporta granularidad real; lo tratamos como off.
          - Cualquier otro valor (``"medium"``, ``"high"``, ``"max"``, futuros) → activo.
        """
        if self.reasoning_effort is None:
            return False
        normalized = self.reasoning_effort.strip().lower()
        return normalized not in ("", "low")


class BaseLLMProvider(ILLMProvider):
    """Clase base para todos los proveedores LLM. Define contrato común.

    ``REQUIRES_CREDENTIALS`` indica si la factory debe exigir una entrada en
    ``providers:`` al resolver las creds. Providers locales (ollama) lo
    override a ``False`` para permitir arrancar sin registry.
    """

    REQUIRES_CREDENTIALS: bool = True

    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        """Declara la signature común de los providers para que la factory
        pueda instanciar `adapter_type(resolved)` con type-check correcto.
        Las subclases override este __init__ para hacer su setup específico
        (httpx client, headers, etc.), no llaman super().__init__()."""
        self._cfg = cfg

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
                # ``reasoning_content`` se inyecta solo cuando el message tiene
                # thinking (transitorio del tool loop). Providers que no
                # entienden el campo lo ignoran silenciosamente.
                entry: dict = {
                    "role": "assistant",
                    "content": m.content if m.content else None,
                    "tool_calls": m.tool_calls,
                }
                if m.thinking:
                    entry["reasoning_content"] = m.thinking
                result.append(entry)
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
    def stream(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        """Stream de chunks de texto. Ver docstring en
        ``core.ports.outbound.llm_port.ILLMProvider.stream`` para el detalle
        de por qué se declara como ``def`` y no ``async def``."""
        ...
