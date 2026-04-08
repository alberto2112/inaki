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
