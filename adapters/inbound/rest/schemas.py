from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


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
