from datetime import datetime
import uuid
from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    embedding: list[float]
    relevance: float  # 0.0 – 1.0, estimada por el LLM extractor
    tags: list[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    agent_id: str | None = None  # None = recuerdo global compartido
    channel: str | None = None  # canal de origen (ej: "telegram", "cli"); None = pre-migración / global
    chat_id: str | None = None  # identificador del chat dentro del canal; None = pre-migración / global
    deleted: bool = False  # soft-delete flag — entries con deleted=True no participan en search/get_recent
