"""ToolRegistry — registro y ejecución de tools."""

from __future__ import annotations

import hashlib
import logging

from adapters.outbound.embedding import resolve_provider_name
from core.domain.services.similarity import cosine_similarity
from core.ports.outbound.embedding_cache_port import IEmbeddingCache
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.tool_port import ITool, IToolExecutor, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry(IToolExecutor):
    def __init__(
        self,
        embedder: IEmbeddingProvider,
        cache: IEmbeddingCache | None = None,
        dimension: int = 384,
    ) -> None:
        self._tools: dict[str, ITool] = {}
        self._embedder = embedder
        self._cache = cache
        self._dimension = dimension
        self._provider_name = resolve_provider_name(embedder)
        self._embeddings: dict[str, list[float]] = {}
        self._embeddings_ready = False

    def register(self, tool: ITool) -> None:
        self._tools[tool.name] = tool
        self._embeddings_ready = False  # invalidar cache si se registran más tools
        logger.debug("Tool registrada: '%s'", tool.name)

    async def _ensure_embeddings(self) -> None:
        if self._embeddings_ready:
            return
        for tool in self._tools.values():
            if tool.name in self._embeddings:
                continue
            content_hash = hashlib.md5(tool.description.encode("utf-8")).hexdigest()

            embedding: list[float] | None = None
            if self._cache is not None:
                embedding = await self._cache.get(
                    content_hash, self._provider_name, self._dimension
                )

            if embedding is None:
                embedding = await self._embedder.embed_passage(tool.description)
                if self._cache is not None:
                    await self._cache.put(
                        content_hash, self._provider_name, self._dimension, embedding
                    )

            self._embeddings[tool.name] = embedding
        self._embeddings_ready = True
        logger.debug("Embeddings de tools generados: %d tools", len(self._embeddings))

    async def execute(self, tool_name: str, **kwargs) -> ToolResult:
        if tool_name not in self._tools:
            return ToolResult(
                tool_name=tool_name,
                output=f"Tool '{tool_name}' no encontrada. Tools disponibles: {', '.join(self._tools.keys())}",
                success=False,
                error=f"Tool no registrada: {tool_name}",
                retryable=False,
            )
        try:
            return await self._tools[tool_name].execute(**kwargs)
        except Exception as exc:
            logger.exception("Error ejecutando tool '%s'", tool_name)
            return ToolResult(
                tool_name=tool_name,
                output=f"Error interno en '{tool_name}': {exc}",
                success=False,
                error=str(exc),
                retryable=False,
            )

    def _schema_dict(self, tool: ITool) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema,
            },
        }

    def get_schemas(self) -> list[dict]:
        return [self._schema_dict(tool) for tool in self._tools.values()]

    async def _rank_tools_by_query(
        self,
        query_embedding: list[float],
        top_k: int,
        min_score: float,
    ) -> list[tuple[str, float]]:
        await self._ensure_embeddings()
        if not self._embeddings:
            return []

        scored = [
            (name, cosine_similarity(query_embedding, emb))
            for name, emb in self._embeddings.items()
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        if min_score > 0.0:
            scored = [(name, s) for name, s in scored if s >= min_score]
        return scored[:top_k]

    async def get_schemas_relevant_with_scores(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[tuple[dict, float]]:
        ranked = await self._rank_tools_by_query(query_embedding, top_k, min_score)
        out: list[tuple[dict, float]] = []
        for name, score in ranked:
            tool = self._tools.get(name)
            if tool is not None:
                out.append((self._schema_dict(tool), score))
        return out

    async def get_schemas_relevant(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[dict]:
        ranked = await self._rank_tools_by_query(query_embedding, top_k, min_score)
        top_names = {name for name, _ in ranked}
        return [self._schema_dict(tool) for tool in self._tools.values() if tool.name in top_names]
