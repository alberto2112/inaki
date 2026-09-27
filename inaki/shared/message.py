from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    TOOL_RESULT = "tool_result"


class Message(BaseModel):
    role: Role
    content: str
    timestamp: datetime | None = None
    # Campos para el protocolo de tool calls (solo en working_messages del tool loop,
    # nunca se persisten en historial).
    tool_calls: list[dict] | None = None  # assistant message con tool calls
    tool_call_id: str | None = None  # tool result vinculado a un tool call
    # Cadena de razonamiento del LLM (DeepSeek thinking mode, o-series, etc.).
    # Mismo patrón que tool_calls: vive en working_messages, se re-inyecta como
    # ``reasoning_content`` en el payload al provider, y se descarta al final
    # del tool loop. NUNCA se persiste.
    thinking: str | None = None
    # Contenido crudo del assistant tal como lo devolvió el provider, OPACO para
    # el kernel. Existe para providers que exigen recibir de vuelta, sin tocar,
    # bloques que el dominio no modela (Anthropic: ``thinking`` con su
    # ``signature``, obligatorio en el tool loop). Mismo ciclo de vida que
    # ``thinking``: vive en working_messages del turno en vuelo y NUNCA se
    # persiste. Los providers que no lo entienden lo ignoran.
    provider_content: dict | None = None
    # Scope del mensaje cuando viene del historial. None en working_messages del
    # tool loop o en mensajes que aún no se persistieron. Permite agrupar por
    # conversación al consolidar memoria sin necesidad de un nuevo entity.
    channel: str | None = None
    chat_id: str | None = None
