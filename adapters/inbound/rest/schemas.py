from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    """Body para POST /chat."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, description="Mensaje del usuario")
    channel: str | None = Field(
        None,
        description=(
            "Canal de origen del turno (ej. 'telegram', 'cli'). "
            "Determina el channel_type del ChannelContext y el scope del historial. "
            "Debe acompañarse de chat_id. Si se omite, scope legacy ('', '')."
        ),
    )
    chat_id: str | None = Field(
        None,
        description=("Identificador del chat dentro del canal. Debe acompañarse de channel."),
    )

    @model_validator(mode="after")
    def _validate_scope_pair(self) -> "ChatRequest":
        if (self.channel is None) != (self.chat_id is None):
            raise ValueError("channel y chat_id deben enviarse juntos o ambos omitirse")
        return self


class ChatResponse(BaseModel):
    agent_id: str
    agent_name: str
    response: str


class AgentInfo(BaseModel):
    id: str
    name: str
    description: str


class HistoryResponse(BaseModel):
    agent_id: str
    messages: list[dict]


class ConsolidateResponse(BaseModel):
    result: str
