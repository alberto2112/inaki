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
    channel: str | None = Field(
        None,
        description=(
            "Canal de origen del turno (ej. 'telegram', 'cli'). "
            "Determina el channel_type del ChannelContext y el scope del historial. "
            "Debe acompañarse de chat_id. Si se omite, channel_type='cli' y scope legacy ('', '')."
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
    def _validate_scope_pair(self) -> "ChatTurnRequest":
        if (self.channel is None) != (self.chat_id is None):
            raise ValueError("channel y chat_id deben enviarse juntos o ambos omitirse")
        return self


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


# ---------------------------------------------------------------------------
# Tool endpoints schemas (Fase 5 — backend tools/send)
# ---------------------------------------------------------------------------


class ToolListEntry(BaseModel):
    """Entrada de una tool en la lista de tools del agente."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Nombre de la tool")
    description: str = Field(..., description="Descripción de la tool (para el LLM)")
    parameters_schema: dict = Field(..., description="JSON Schema de los parámetros")


class ToolListResponse(BaseModel):
    """Respuesta de GET /admin/tool/list."""

    tools: list[ToolListEntry] = Field(
        default_factory=list, description="Tools registradas en el agente"
    )


class ToolInvokeRequest(BaseModel):
    """Body para POST /admin/tool/invoke."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="ID del agente cuya tool se invoca")
    tool_name: str = Field(..., min_length=1, description="Nombre de la tool a invocar")
    args: dict = Field(default_factory=dict, description="Argumentos para la tool")


class ToolInvokeResponse(BaseModel):
    """Respuesta de POST /admin/tool/invoke."""

    tool_name: str = Field(..., description="Nombre de la tool invocada")
    output: str = Field(
        ..., description="Output de la tool (serialización opaca, normalmente JSON)"
    )
    success: bool = Field(..., description="True si la tool ejecutó sin error")
    error: str | None = Field(None, description="Mensaje de error si success=False")


# ---------------------------------------------------------------------------
# Send endpoint schemas (Fase 5 — /admin/send)
# ---------------------------------------------------------------------------

_MEDIA_SINGLE_KINDS = {"photo", "audio", "video", "file"}
_ALL_MEDIA_KINDS = _MEDIA_SINGLE_KINDS | {"album"}


class SendRequest(BaseModel):
    """Body para POST /admin/send."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="ID del agente desde el que se envía")
    channel: str = Field(..., min_length=1, description="Canal destino (ej. 'telegram')")
    chat_id: str = Field(..., min_length=1, description="Identificador del chat dentro del canal")
    kind: str = Field(
        ...,
        description="Tipo de contenido: text | photo | audio | video | file | album",
    )
    text: str | None = Field(None, description="Texto del mensaje (requerido si kind=text)")
    sources: list[str] | None = Field(
        None, description="Paths locales de archivos a enviar (requeridos para media)"
    )
    caption: str | None = Field(None, description="Texto descriptivo adjunto a un archivo/álbum")

    broadcast: bool = Field(
        default=True,
        description=(
            "Si emitir BroadcastMessage al LAN tras envío exitoso (solo aplica "
            "para kind=text y channel=telegram). Default True (consistente con "
            "el comportamiento del bot)."
        ),
    )

    @model_validator(mode="after")
    def _validate_kind_payload(self) -> "SendRequest":
        """Valida coherencia entre kind y los campos text/sources/caption."""
        kind = self.kind
        valid_kinds = {"text"} | _ALL_MEDIA_KINDS
        if kind not in valid_kinds:
            raise ValueError(f"kind inválido: '{kind}'. Valores permitidos: {sorted(valid_kinds)}")
        if kind == "text":
            if not self.text:
                raise ValueError("kind=text requiere el campo 'text' no vacío")
            if self.sources is not None:
                raise ValueError("kind=text no admite 'sources'")
            if self.caption is not None:
                raise ValueError("kind=text no admite 'caption'")
        elif kind in _MEDIA_SINGLE_KINDS:
            if not self.sources or len(self.sources) != 1:
                raise ValueError(f"kind={kind} requiere exactamente 1 path en 'sources'")
        elif kind == "album":
            if not self.sources or len(self.sources) < 1:
                raise ValueError("kind=album requiere al menos 1 path en 'sources'")
        return self


class SendResponse(BaseModel):
    """Respuesta de POST /admin/send."""

    sent: bool = Field(True, description="Siempre True en respuesta exitosa")
    channel: str = Field(..., description="Canal al que se envió")
    chat_id: str = Field(..., description="Chat destino")
    kind: str = Field(..., description="Tipo de contenido enviado")
    broadcasted: bool = Field(
        default=False,
        description="True si el mensaje se emitió al canal de broadcast LAN.",
    )
