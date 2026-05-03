"""Schemas del admin REST server."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HealthResponse(BaseModel):
    status: str = "ok"


class SchedulerReloadResponse(BaseModel):
    reloaded: bool = True


class DaemonReloadResponse(BaseModel):
    """Respuesta de POST /admin/reload — el daemon va a cerrar canales y volver a levantar."""

    reloading: bool = True


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
    intermediates: list[str] = Field(
        default_factory=list,
        description=(
            "Bloques de texto que el LLM emitió junto con tool_calls durante el "
            'turno (narración tipo "ok, voy a buscar esto..."). En orden de '
            "emisión. El cliente los muestra ANTES de ``reply`` para que el "
            "usuario vea el progreso del turno."
        ),
    )


class TaskTurnRequest(BaseModel):
    """Body para POST /admin/chat/task."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="ID del agente al que se envía la tarea")
    message: str = Field(
        ..., min_length=1, description="Tarea a ejecutar (oneshot, sin persistencia)"
    )
    channel: str | None = Field(
        None,
        description=(
            "Canal de origen del scope a cargar (ej. 'telegram', 'cli'). "
            "Debe acompañarse de chat_id. Si se omite, se usa el scope vacío legacy."
        ),
    )
    chat_id: str | None = Field(
        None,
        description=(
            "Identificador del chat dentro del canal (ej. id de grupo de Telegram). "
            "Debe acompañarse de channel."
        ),
    )

    @model_validator(mode="after")
    def _validate_scope_pair(self) -> "TaskTurnRequest":
        # both-or-none: channel y chat_id deben venir juntos o no venir
        if (self.channel is None) != (self.chat_id is None):
            raise ValueError("channel y chat_id deben enviarse juntos o ambos omitirse")
        return self


class TaskTurnResponse(BaseModel):
    """Respuesta de POST /admin/chat/task."""

    reply: str = Field(..., description="Respuesta final del asistente")
    agent_id: str = Field(..., description="ID del agente que respondió")
    intermediates: list[str] = Field(
        default_factory=list,
        description=(
            "Bloques de texto que el LLM emitió junto con tool_calls durante el turno. "
            "En orden de emisión."
        ),
    )


class HistoryMessage(BaseModel):
    """DTO plano de un mensaje del historial."""

    role: str = Field(..., description="Rol del mensaje: user, assistant, system, tool")
    content: str = Field(..., description="Contenido del mensaje")
    timestamp: datetime | None = Field(
        None, description="Marca de tiempo del mensaje (ISO 8601 UTC)"
    )


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
