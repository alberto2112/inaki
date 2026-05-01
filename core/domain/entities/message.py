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
    # Scope del mensaje cuando viene del historial. None en working_messages del
    # tool loop o en mensajes que aún no se persistieron. Permite agrupar por
    # conversación al consolidar memoria sin necesidad de un nuevo entity.
    channel: str | None = None
    chat_id: str | None = None
