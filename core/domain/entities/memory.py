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
