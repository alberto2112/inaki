"""Schemas del admin REST server."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class SchedulerReloadResponse(BaseModel):
    reloaded: bool = True


class InspectRequest(BaseModel):
    agent_id: str
    mensaje: str


class ConsolidateRequest(BaseModel):
    agent_id: str | None = None


class ConsolidateResponse(BaseModel):
    resultado: str


# ---------------------------------------------------------------------------
# Chat endpoints schemas (Sección 5 — cli-chat-via-rest)
# ---------------------------------------------------------------------------


class ChatTurnRequest(BaseModel):
    """Body para POST /admin/chat/turn."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="ID del agente al que se envía el mensaje")
    session_id: str = Field(
        ..., min_length=1, description="UUID de sesión generado por el cliente CLI"
    )
    message: str = Field(..., min_length=1, description="Mensaje del usuario")


class ChatTurnResponse(BaseModel):
    """Respuesta de POST /admin/chat/turn."""

    reply: str = Field(..., description="Respuesta final del asistente")
    agent_id: str = Field(..., description="ID del agente que respondió")
    session_id: str = Field(..., description="UUID de sesión (echo del request)")


class HistoryMessage(BaseModel):
    """DTO plano de un mensaje del historial."""

    role: str = Field(..., description="Rol del mensaje: user, assistant, system, tool")
    content: str = Field(..., description="Contenido del mensaje")
    timestamp: datetime | None = Field(None, description="Marca de tiempo del mensaje (ISO 8601 UTC)")


class HistoryResponse(BaseModel):
    """Respuesta de GET /admin/chat/history."""

    agent_id: str = Field(..., description="ID del agente consultado")
    messages: list[HistoryMessage] = Field(
        default_factory=list, description="Mensajes en orden cronológico"
    )


class AgentsResponse(BaseModel):
    """Respuesta de GET /admin/agents — lista de agentes registrados en el daemon."""

    agents: list[str] = Field(
        default_factory=list, description="Lista de IDs de agentes registrados"
    )
