from abc import ABC, abstractmethod
from pydantic import BaseModel


class ToolResult(BaseModel):
    tool_name: str
    output: str
    success: bool
    error: str | None = None


class ITool(ABC):
    name: str
    description: str
    parameters_schema: dict  # JSON Schema

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...


class IToolExecutor(ABC):

    @abstractmethod
    def register(self, tool: ITool) -> None: ...

    @abstractmethod
    async def execute(self, tool_name: str, **kwargs) -> ToolResult: ...

    @abstractmethod
    def get_schemas(self) -> list[dict]:
        """Retorna los schemas de todas las tools registradas para el LLM."""
        ...

    @abstractmethod
    async def get_schemas_relevant(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[dict]:
        """Retorna los schemas de las tools más relevantes para el query via cosine similarity."""
        ...

    @abstractmethod
    async def get_schemas_relevant_with_scores(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[tuple[dict, float]]:
        """Schemas seleccionados por RAG con score de similitud coseno (orden descendente por score)."""
        ...
