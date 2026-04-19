"""MemorySearchTool — búsqueda semántica en la memoria a largo plazo del agente."""

from __future__ import annotations

import logging

from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.memory_port import IMemoryRepository
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


class MemorySearchTool(ITool):
    name = "mem_search"
    description = (
        "Search relevant memories in the agent's long-term memory using semantic similarity. "
        "Use this tool when the user asks about something that may have been mentioned before, "
        "or when you need prior context about a topic. "
        "Required parameter: 'query' (search text). "
        "Optional parameter: 'top_k' (max number of results, default 5)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Semantic search text in memory.",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of memories to return (default: 5).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, memory: IMemoryRepository, embedder: IEmbeddingProvider) -> None:
        self._memory = memory
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

        top_k = int(kwargs.get("top_k") or 5)
        top_k = max(1, min(top_k, 20))

        try:
            query_embedding = await self._embedder.embed_query(query)
            entries = await self._memory.search(query_embedding, top_k=top_k)
        except Exception as exc:
            logger.exception("MemorySearchTool: error searching '%s'", query)
            return ToolResult(
                tool_name=self.name,
                output=f"Error searching in memory: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        if not entries:
            return ToolResult(
                tool_name=self.name,
                output="No relevant memories found for that search.",
                success=True,
            )

        lines = [f"Found {len(entries)} relevant memory(ies):\n"]
        for i, entry in enumerate(entries, 1):
            tags_str = f" [{', '.join(entry.tags)}]" if entry.tags else ""
            date = entry.created_at.strftime("%Y-%m-%d")
            lines.append(f"{i}. [{date}]{tags_str} {entry.content}")

        return ToolResult(
            tool_name=self.name,
            output="\n".join(lines),
            success=True,
        )
