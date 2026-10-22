"""tool_output — tool builtin que recupera el output COMPLETO de un tool result truncado.

El historial guarda los tool results acotados a ``persist_tool_result_max_chars``
(cabeza + cola, con un marcador en el medio que trae una ``ref``). El output entero
queda en la misma fila de ``history.db`` y esta tool lo devuelve por esa ``ref``,
paginado para no volver a inundar el contexto con un volcado de cientos de KB.

Nadie la pinnea: el LLM la conoce por el marcador y la alcanza por page-in
(``docs/semantic-routing.md``). Scopeada al ``agent_id``, como ``search_history``.
Ver la nota ``tool-output-completo``.
"""

from __future__ import annotations

import logging

from inaki.kernel.ports.history_port import IHistoryStore
from inaki.kernel.ports.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)

# Tamaño de página: lo bastante grande para que la mayoría de los outputs entren de
# una, lo bastante chico para que un volcado enorme no se coma el contexto del turno.
_PAGE_CHARS = 16000

# Una ref que no está NO prueba que el output no existiera: su fila la pudo borrar
# la consolidación (``trim``) o un ``/clear``. Decirlo evita que el modelo convierta
# "ya no lo tengo" en "no pasó" (nota ``search-history-retention-horizon``).
_NOT_FOUND = (
    "No stored output for ref '{ref}'. Either the ref is wrong or the history row "
    "that held it was already deleted by memory consolidation or a /clear. This "
    "proves NOTHING about what the tool returned back then: say you no longer have "
    "the full output, and re-run the action only if it is safe to repeat."
)


class ToolOutputTool(ITool):
    name = "tool_output"
    description = (
        "Retrieve the FULL output of a past tool call whose result was truncated in "
        "the conversation history. Truncated results contain a marker like "
        '`[... chars omitted. Full output still stored: call tool_output with ref="abc123"]`; '
        "pass that ref. Use it to verify a detail from an earlier tool result (an exit "
        "code, a status field, a line of a log) instead of guessing or repeating the "
        "action. Long outputs are paginated: if the result says it continues, call "
        "again with the given 'offset'."
    )
    routing_keywords = (
        "output completo, resultado truncado, ver el resultado entero, chars omitidos, "
        "qué devolvió la herramienta, verificar el resultado anterior. "
        "full output, truncated result, chars omitted, see the whole result, what the "
        "tool returned, verify the earlier result. "
        "sortie complète, résultat tronqué, caractères omis, voir le résultat entier."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "The ref shown in the truncation marker of the tool result.",
            },
            "offset": {
                "type": "integer",
                "description": "Character offset to start reading from (default 0). "
                "Use the value the previous page told you.",
            },
        },
        "required": ["ref"],
    }

    def __init__(self, history: IHistoryStore, agent_id: str) -> None:
        self._history = history
        self._agent_id = agent_id

    async def execute(self, **kwargs) -> ToolResult:
        ref = str(kwargs.get("ref") or "").strip().strip('"')
        if not ref:
            return ToolResult(
                tool_name=self.name,
                output="The 'ref' parameter is required (copy it from the truncation marker).",
                success=False,
                error="missing ref",
                retryable=False,
            )
        try:
            offset = max(0, int(kwargs.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0

        try:
            content = await self._history.get_tool_output(self._agent_id, ref)
        except Exception as exc:
            logger.exception("ToolOutputTool: error leyendo el output archivado")
            return ToolResult(
                tool_name=self.name,
                output=f"Error reading the stored output: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        if content is None:
            return ToolResult(tool_name=self.name, output=_NOT_FOUND.format(ref=ref), success=True)

        total = len(content)
        if offset >= total:
            return ToolResult(
                tool_name=self.name,
                output=f"Offset {offset} is past the end: the output has {total} chars.",
                success=False,
                error="offset out of range",
                retryable=False,
            )

        fin = min(offset + _PAGE_CHARS, total)
        cabecera = f"Full output of ref '{ref}': chars {offset}-{fin} of {total}."
        partes = [cabecera, content[offset:fin]]
        if fin < total:
            partes.append(f'[continues: call tool_output with ref="{ref}" and offset={fin}]')
        return ToolResult(tool_name=self.name, output="\n".join(partes), success=True)
