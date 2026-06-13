from abc import ABC, abstractmethod
from pydantic import BaseModel


class ToolResult(BaseModel):
    tool_name: str
    output: str
    success: bool
    error: str | None = None
    retryable: bool = True


class ITool(ABC):
    name: str
    description: str
    parameters_schema: dict  # JSON Schema

    # Disparadores adicionales SOLO para el embedding del semantic routing.
    # NO se muestran al LLM (no van al schema): el LLM recibe únicamente
    # `description`. Su único propósito es enriquecer el texto que se embebe
    # para el matching coseno, mejorando el retrieval cross-lingual.
    #
    # Patrón recomendado: `description` en inglés (comprensión óptima del LLM)
    # y `routing_keywords` con disparadores MULTILINGÜES (es/en/fr) que reflejen
    # cómo un humano expresa la intención de usar la tool. multilingual-e5-small
    # matchea mucho mejor query↔texto dentro del mismo idioma que cruzando idiomas.
    #
    # Default "" → tools que no lo definan se comportan como antes (solo
    # `description` se embebe). 100% backward-compat.
    routing_keywords: str = ""

    # Tool Config Protocol (ver core/ports/outbound/tool_config_port.py).
    # Namespace del bloque tool_config.{namespace} en config/tool_config.yaml.
    # Si una tool lo declara (no-vacío), el container la instancia con el
    # kwarg `config_store` (IToolConfigStore) — incluidas las tools de ext/.
    # Default "" → la tool no usa el protocolo y se instancia sin args.
    config_namespace: str = ""

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
