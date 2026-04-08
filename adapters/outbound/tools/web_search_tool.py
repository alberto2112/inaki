"""WebSearchTool — búsqueda web via DuckDuckGo."""

from __future__ import annotations

import logging

from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


class WebSearchTool(ITool):
    name = "web_search"
    description = (
        "Busca información en internet usando DuckDuckGo. "
        "Usar para preguntas sobre eventos actuales, datos que pueden cambiar, "
        "o información que no está en el contexto."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "La consulta de búsqueda",
            },
            "max_results": {
                "type": "integer",
                "description": "Número máximo de resultados (default: 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    async def execute(self, query: str, max_results: int = 5, **kwargs) -> ToolResult:
        logger.info("WebSearchTool: '%s'", query)
        try:
            from duckduckgo_search import DDGS
            import asyncio

            def _search():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=max_results))

            results = await asyncio.get_event_loop().run_in_executor(None, _search)

            if not results:
                return ToolResult(
                    tool_name=self.name,
                    output="No se encontraron resultados.",
                    success=True,
                )

            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r.get('title', '')}")
                lines.append(f"   {r.get('href', '')}")
                lines.append(f"   {r.get('body', '')}")
                lines.append("")

            return ToolResult(
                tool_name=self.name,
                output="\n".join(lines).strip(),
                success=True,
            )
        except ImportError:
            return ToolResult(
                tool_name=self.name,
                output="duckduckgo-search no instalado.",
                success=False,
                error="missing dependency: duckduckgo-search",
            )
        except Exception as exc:
            logger.exception("WebSearchTool error")
            return ToolResult(
                tool_name=self.name,
                output=f"Error en búsqueda: {exc}",
                success=False,
                error=str(exc),
            )
