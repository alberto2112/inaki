"""search_history — tool builtin para consultar el historial CRUDO de conversación.

A diferencia de ``search_memory`` (hechos extraídos a largo plazo), esta tool
devuelve los mensajes TAL CUAL se dijeron, leyendo el ``IHistoryStore`` del
agente. Reemplaza la vieja extensión ``ext/search_history``, que abría el SQLite
por path a mano (sin filtro de ``agent_id``, path hardcodeado, API de ``ToolResult``
inexistente): acá la capacidad vive en el port y la tool solo la envuelve,
scopeada por ``agent_id`` — un agente nunca lee el historial de otro.

Nota de privacidad: la tool puede leer CUALQUIER conversación del mismo
``agent_id`` (cualquier chat/usuario que habló con este agente). Es la capacidad
que se pidió (auditar la conversación con otro usuario) y queda acotada al
agente. Si en el futuro se abre a terceros, conviene restringir por scope.
"""

from __future__ import annotations

import logging

from core.domain.entities.message import Message
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


class SearchHistoryTool(ITool):
    name = "search_history"
    description = (
        "Retrieve the EXACT, verbatim text of messages from a specific past "
        "conversation in this agent's history database — word for word, in "
        "chronological order, including another user's chat. "
        "Use this when you need what was LITERALLY said (the actual message text), "
        "to review a particular conversation, or to find who said what and when. "
        "NOT `search_memory` (which returns distilled long-term FACTS about the "
        "user) and NOT `knowledge_search` (documents / fuzzy semantic recall): "
        "this returns the real messages, not summaries. "
        "All filters optional: 'query' (substring match on message text), 'role' "
        "(user/assistant), 'channel' (e.g. 'telegram', 'cli'), 'chat_id' (target "
        "ONE conversation; omit to search all of this agent's history), 'limit' "
        "(max results, default 20, capped at 100). Without 'query' it returns the "
        "most recent messages of the matched scope, most-recent first, each "
        "annotated with its origin scope."
    )
    # Disparadores multilingües SOLO para el embedding del semantic routing
    # (no van al schema del LLM). Apuntan a lo LITERAL/textual de un chat puntual,
    # deliberadamente lejos de los genéricos "qué sé / qué hablamos" que colisionan
    # con search_memory (hechos) y knowledge_search (documentos).
    routing_keywords = (
        "palabra por palabra, qué dijo exactamente, qué escribió, mensaje literal, "
        "transcripción del chat, la conversación con, el chat con, revisá el chat de, "
        "qué se dijo exactamente en, mostrame el mensaje donde, quién dijo qué. "
        "exact words, verbatim, word for word, what exactly was said, the conversation "
        "with, that specific chat, transcript, who said what, find the message where, "
        "review the chat with. "
        "mot pour mot, qu'a-t-il dit exactement, message exact, la conversation avec, "
        "transcription du chat, qui a dit quoi."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Substring to search within message text (case-insensitive). Omit to return the most recent messages.",
            },
            "role": {
                "type": "string",
                "enum": ["user", "assistant"],
                "description": "Filter by sender role (optional).",
            },
            "channel": {
                "type": "string",
                "description": "Filter by channel, e.g. 'telegram' or 'cli' (optional).",
            },
            "chat_id": {
                "type": "string",
                "description": "Target a specific conversation by its chat id (optional). Omit to search across all conversations of this agent.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of messages (default 20, max 100).",
            },
        },
        "required": [],
    }

    def __init__(self, history: IHistoryStore, agent_id: str) -> None:
        self._history = history
        self._agent_id = agent_id

    async def execute(self, **kwargs) -> ToolResult:
        query = self._clean(kwargs.get("query"))
        role = self._clean(kwargs.get("role"))
        channel = self._clean(kwargs.get("channel"))
        chat_id = self._clean(kwargs.get("chat_id"))

        if role is not None and role not in ("user", "assistant"):
            return ToolResult(
                tool_name=self.name,
                output="The 'role' filter must be 'user' or 'assistant'.",
                success=False,
                error="invalid role",
                retryable=False,
            )

        limit_raw = kwargs.get("limit")
        try:
            limit = int(limit_raw) if limit_raw is not None else _DEFAULT_LIMIT
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        limit = max(1, min(limit, _MAX_LIMIT))

        try:
            messages = await self._history.search(
                self._agent_id,
                query=query,
                role=role,
                channel=channel,
                chat_id=chat_id,
                limit=limit,
            )
        except Exception as exc:
            logger.exception("SearchHistoryTool: error buscando en el historial")
            return ToolResult(
                tool_name=self.name,
                output=f"Error searching history: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        if not messages:
            detalle = f" matching '{query}'" if query else ""
            return ToolResult(
                tool_name=self.name,
                output=f"No messages found{detalle} for the given filters.",
                success=True,
            )

        lines = [f"Found {len(messages)} message(s) (most recent first):"]
        lines.extend(self._format(msg) for msg in messages)
        return ToolResult(
            tool_name=self.name,
            output="\n".join(lines),
            success=True,
        )

    @staticmethod
    def _clean(value: object) -> str | None:
        """Normaliza un filtro opcional: None/'' → None, resto → texto stripeado."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _format(msg: Message) -> str:
        ts = msg.timestamp.isoformat() if msg.timestamp else "-"
        scope = f"({msg.channel or '-'}, {msg.chat_id or '-'})"
        role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
        return f"[{ts}] {role} scope={scope}\n  {msg.content}"
