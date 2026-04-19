"""KnowledgeSearchTool — búsqueda semántica en las fuentes de conocimiento del agente."""

from __future__ import annotations

import logging

from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_TOP_K = 5
_DEFAULT_MIN_SCORE = 0.0


class KnowledgeSearchTool(ITool):
    name = "knowledge_search"
    description = (
        "Search the agent's knowledge sources (memory and configured knowledge bases) "
        "using semantic similarity. "
        "Use this tool when the user asks about something that may have been mentioned before, "
        "or when you need prior context about a topic. "
        "Required parameter: 'query' (search text). "
        "Optional parameter: 'top_k' (max number of results, default 5). "
        "Optional parameter: 'source' (restrict search to a specific source ID, e.g. 'memory')."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Semantic search query.",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 5).",
            },
            "source": {
                "type": "string",
                "description": (
                    "Optional source ID to restrict the search (e.g. 'memory'). "
                    "If omitted, all available sources are queried."
                ),
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        orchestrator: KnowledgeOrchestrator,
        embedder: IEmbeddingProvider,
    ) -> None:
        self._orchestrator = orchestrator
        self._embedder = embedder

    async def execute(self, **kwargs) -> ToolResult:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(
                tool_name=self.name,
                output="The 'query' parameter is required.",
                success=False,
                error="query empty",
                retryable=False,
            )

        top_k_raw = kwargs.get("top_k")
        top_k = int(top_k_raw) if top_k_raw is not None else _DEFAULT_TOP_K
        top_k = max(1, min(top_k, 20))

        source_filter: str | None = kwargs.get("source") or None

        try:
            query_vec = await self._embedder.embed_query(query)
            chunks = await self._orchestrator.retrieve_all(
                query_vec=query_vec,
                top_k=top_k,
                min_score=_DEFAULT_MIN_SCORE,
            )
        except Exception as exc:
            logger.exception("KnowledgeSearchTool: error buscando '%s'", query)
            return ToolResult(
                tool_name=self.name,
                output=f"Error searching knowledge sources: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        # Filtrar por fuente si se especificó
        if source_filter is not None:
            fuentes_disponibles = set(self._orchestrator.source_ids)
            if source_filter not in fuentes_disponibles:
                return ToolResult(
                    tool_name=self.name,
                    output=f"Unknown source '{source_filter}'. Available sources: {sorted(fuentes_disponibles)}.",
                    success=False,
                    error=f"unknown source: {source_filter}",
                    retryable=False,
                )
            chunks = [c for c in chunks if c.source_id == source_filter]

        if not chunks:
            return ToolResult(
                tool_name=self.name,
                output="No relevant results found for that search.",
                success=True,
            )

        lines = [f"Found {len(chunks)} result(s):\n"]
        for i, chunk in enumerate(chunks, 1):
            lines.append(f"{i}. [{chunk.source_id}] (score={chunk.score:.3f}) {chunk.content}")

        return ToolResult(
            tool_name=self.name,
            output="\n".join(lines),
            success=True,
        )
