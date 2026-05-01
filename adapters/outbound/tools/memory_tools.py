"""
Tools de gestión de memoria a largo plazo expuestas al LLM.

Cubren los tres flujos básicos que faltaban en el sistema:
  - ``search_memory`` — búsqueda semántica directa sobre la memoria del agente
    (no pasa por el orquestrador de knowledge; va al ``IMemoryRepository`` y
    devuelve IDs reales para que el LLM pueda referenciarlos en delete/update).
  - ``delete_memory`` — soft-delete por id. Idempotente; devuelve la entry
    borrada para que el agente pueda confirmar al usuario qué desapareció.
  - ``update_memory`` — edita campos individuales de un recuerdo. Si cambia
    el ``content``, recomputa el embedding y lo persiste atómicamente.

Nota de diseño: estas tools NO filtran por scope ``(channel, chat_id)``. La
memoria es del usuario (no del chat), así que desde cualquier conversación
con el agente puede gestionarse cualquier recuerdo del mismo agent_id. Los
resultados de ``search_memory`` exponen el origen para que el LLM y el
usuario sepan de qué chat venía la entry antes de actuar sobre ella.
"""

from __future__ import annotations

import json
import logging

from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.memory_port import IMemoryRepository
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


_DEFAULT_TOP_K = 5
_MAX_TOP_K = 20


def _format_entry_summary(
    *,
    memory_id: str,
    content: str,
    relevance: float,
    tags: list[str],
    created_at: str,
    channel: str | None,
    chat_id: str | None,
    score: float | None = None,
) -> str:
    """Línea compacta para el output textual del LLM."""
    score_part = f" score={score:.3f}" if score is not None else ""
    scope_part = f" scope=({channel or '-'}, {chat_id or '-'})"
    tags_part = f" tags={tags}" if tags else ""
    return (
        f"id={memory_id} relevance={relevance:.2f}{score_part}{scope_part}"
        f" created_at={created_at}{tags_part}\n  content: {content}"
    )


class SearchMemoryTool(ITool):
    name = "search_memory"
    description = (
        "Search the user's long-term memory for the current agent using semantic "
        "similarity. Use this when the user asks about something that may have "
        "been recorded before, or when you need to FIND the id of a memory "
        "before deleting or updating it. "
        "Required: 'query' (search text). "
        "Optional: 'top_k' (max results, default 5, capped at 20). "
        "Returns each result with its UUID `id`, content, relevance, tags, "
        "creation date, scope (channel + chat_id of origin), and similarity score."
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
                "description": "Maximum number of results (default 5, max 20).",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        memory: IMemoryRepository,
        embedder: IEmbeddingProvider,
    ) -> None:
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

        top_k_raw = kwargs.get("top_k")
        top_k = int(top_k_raw) if top_k_raw is not None else _DEFAULT_TOP_K
        top_k = max(1, min(top_k, _MAX_TOP_K))

        try:
            query_vec = await self._embedder.embed_query(query)
            scored = await self._memory.search_with_scores(query_vec, top_k=top_k)
        except Exception as exc:
            logger.exception("SearchMemoryTool: error buscando '%s'", query)
            return ToolResult(
                tool_name=self.name,
                output=f"Error searching memory: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        if not scored:
            return ToolResult(
                tool_name=self.name,
                output="No memories matched that query.",
                success=True,
            )

        lines = [f"Found {len(scored)} memory entries:"]
        for entry, score in scored:
            lines.append(
                _format_entry_summary(
                    memory_id=entry.id,
                    content=entry.content,
                    relevance=entry.relevance,
                    tags=entry.tags,
                    created_at=entry.created_at.isoformat(),
                    channel=entry.channel,
                    chat_id=entry.chat_id,
                    score=score,
                )
            )

        return ToolResult(
            tool_name=self.name,
            output="\n".join(lines),
            success=True,
        )


class DeleteMemoryTool(ITool):
    name = "delete_memory"
    description = (
        "Soft-delete a memory entry by its UUID id. The entry stops appearing "
        "in future searches and digests but is kept in storage (reversible by "
        "the operator). Use this only when the user explicitly asks to delete "
        "or correct a memory. Always call `search_memory` first to obtain the "
        "exact `id` — never invent UUIDs. "
        "Required: 'memory_id' (UUID returned by search_memory). "
        "Returns a confirmation with the deleted content so you can echo back "
        "to the user what was removed."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "UUID of the memory entry to soft-delete.",
            },
        },
        "required": ["memory_id"],
    }

    def __init__(self, memory: IMemoryRepository) -> None:
        self._memory = memory

    async def execute(self, **kwargs) -> ToolResult:
        memory_id = str(kwargs.get("memory_id") or "").strip()
        if not memory_id:
            return ToolResult(
                tool_name=self.name,
                output="The 'memory_id' parameter is required.",
                success=False,
                error="memory_id empty",
                retryable=False,
            )

        try:
            entry = await self._memory.delete(memory_id)
        except Exception as exc:
            logger.exception("DeleteMemoryTool: error borrando '%s'", memory_id)
            return ToolResult(
                tool_name=self.name,
                output=f"Error deleting memory '{memory_id}': {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        if entry is None:
            return ToolResult(
                tool_name=self.name,
                output=(
                    f"No active memory with id '{memory_id}' (already deleted "
                    "or never existed). No-op."
                ),
                success=True,
            )

        return ToolResult(
            tool_name=self.name,
            output=(
                f"Deleted memory id={entry.id}\n"
                f"  content: {entry.content}\n"
                f"  scope: ({entry.channel or '-'}, {entry.chat_id or '-'})"
            ),
            success=True,
        )


class UpdateMemoryTool(ITool):
    name = "update_memory"
    description = (
        "Edit fields of an existing memory entry. Only provided fields are "
        "modified; omitted fields stay untouched. If you change the `content`, "
        "the embedding is automatically recomputed and persisted. Cannot edit "
        "deleted entries. Always call `search_memory` first to obtain the id. "
        "Required: 'memory_id'. "
        "Optional (at least one): 'content' (new text), 'tags' (list of strings), "
        "'relevance' (0.0-1.0). "
        "Returns the updated entry."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "UUID of the memory entry to update.",
            },
            "content": {
                "type": "string",
                "description": "New content. Triggers embedding recomputation.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "New tags list (replaces the existing one).",
            },
            "relevance": {
                "type": "number",
                "description": "New relevance score (0.0-1.0).",
            },
        },
        "required": ["memory_id"],
    }

    def __init__(
        self,
        memory: IMemoryRepository,
        embedder: IEmbeddingProvider,
    ) -> None:
        self._memory = memory
        self._embedder = embedder

    async def execute(self, **kwargs) -> ToolResult:
        memory_id = str(kwargs.get("memory_id") or "").strip()
        if not memory_id:
            return ToolResult(
                tool_name=self.name,
                output="The 'memory_id' parameter is required.",
                success=False,
                error="memory_id empty",
                retryable=False,
            )

        content_raw = kwargs.get("content")
        tags_raw = kwargs.get("tags")
        relevance_raw = kwargs.get("relevance")

        if content_raw is None and tags_raw is None and relevance_raw is None:
            return ToolResult(
                tool_name=self.name,
                output=(
                    "At least one of 'content', 'tags' or 'relevance' must be "
                    "provided."
                ),
                success=False,
                error="no fields to update",
                retryable=False,
            )

        content: str | None = None
        if content_raw is not None:
            content = str(content_raw).strip()
            if not content:
                return ToolResult(
                    tool_name=self.name,
                    output="'content' cannot be empty.",
                    success=False,
                    error="content empty",
                    retryable=False,
                )

        tags: list[str] | None = None
        if tags_raw is not None:
            if not isinstance(tags_raw, list):
                return ToolResult(
                    tool_name=self.name,
                    output="'tags' must be a list of strings.",
                    success=False,
                    error="tags not a list",
                    retryable=False,
                )
            tags = [str(t) for t in tags_raw]

        relevance: float | None = None
        if relevance_raw is not None:
            try:
                relevance = float(relevance_raw)
            except (TypeError, ValueError):
                return ToolResult(
                    tool_name=self.name,
                    output="'relevance' must be a number between 0.0 and 1.0.",
                    success=False,
                    error="relevance not a number",
                    retryable=False,
                )
            if not (0.0 <= relevance <= 1.0):
                return ToolResult(
                    tool_name=self.name,
                    output="'relevance' must be between 0.0 and 1.0.",
                    success=False,
                    error="relevance out of range",
                    retryable=False,
                )

        try:
            embedding: list[float] | None = None
            if content is not None:
                embedding = await self._embedder.embed_passage(content)
            entry = await self._memory.update(
                memory_id,
                content=content,
                tags=tags,
                relevance=relevance,
                embedding=embedding,
            )
        except Exception as exc:
            logger.exception("UpdateMemoryTool: error actualizando '%s'", memory_id)
            return ToolResult(
                tool_name=self.name,
                output=f"Error updating memory '{memory_id}': {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        if entry is None:
            return ToolResult(
                tool_name=self.name,
                output=(
                    f"No active memory with id '{memory_id}' (deleted or never "
                    "existed). Update aborted."
                ),
                success=False,
                error="not found",
                retryable=False,
            )

        return ToolResult(
            tool_name=self.name,
            output=(
                f"Updated memory id={entry.id}\n"
                f"  content: {entry.content}\n"
                f"  relevance: {entry.relevance:.2f}\n"
                f"  tags: {json.dumps(entry.tags, ensure_ascii=False)}\n"
                f"  scope: ({entry.channel or '-'}, {entry.chat_id or '-'})"
            ),
            success=True,
        )
