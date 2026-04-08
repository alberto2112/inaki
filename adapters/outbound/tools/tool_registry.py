"""ToolRegistry — registro y ejecución de tools."""

from __future__ import annotations

import logging

import numpy as np

from core.domain.errors import ToolError
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.tool_port import ITool, IToolExecutor, ToolResult

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


class ToolRegistry(IToolExecutor):

    def __init__(self, embedder: IEmbeddingProvider) -> None:
        self._tools: dict[str, ITool] = {}
        self._embedder = embedder
        self._embeddings: dict[str, list[float]] = {}
        self._embeddings_ready = False

    def register(self, tool: ITool) -> None:
        self._tools[tool.name] = tool
        self._embeddings_ready = False  # invalidar cache si se registran más tools
        logger.debug("Tool registrada: '%s'", tool.name)

    async def _ensure_embeddings(self) -> None:
        if self._embeddings_ready:
            return
        self._embeddings = {}
        for tool in self._tools.values():
            embedding = await self._embedder.embed_passage(tool.description)
            self._embeddings[tool.name] = embedding
        self._embeddings_ready = True
        logger.debug("Embeddings de tools generados: %d tools", len(self._embeddings))

    async def execute(self, tool_name: str, **kwargs) -> ToolResult:
        if tool_name not in self._tools:
            return ToolResult(
                tool_name=tool_name,
                output=f"Tool '{tool_name}' no encontrada",
                success=False,
                error=f"Tool no registrada: {tool_name}",
            )
        try:
            return await self._tools[tool_name].execute(**kwargs)
        except Exception as exc:
            logger.exception("Error ejecutando tool '%s'", tool_name)
            return ToolResult(
                tool_name=tool_name,
                output=f"Error: {exc}",
                success=False,
                error=str(exc),
            )

    def get_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                },
            }
            for tool in self._tools.values()
        ]

    async def get_schemas_relevant(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        await self._ensure_embeddings()
        if not self._embeddings:
            return []

        scored = [
            (name, _cosine_similarity(query_embedding, emb))
            for name, emb in self._embeddings.items()
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        top_names = {name for name, _ in scored[:top_k]}

        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                },
            }
            for tool in self._tools.values()
            if tool.name in top_names
        ]
