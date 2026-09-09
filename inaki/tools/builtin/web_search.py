"""WebSearchTool — búsqueda web via Tavily API.

Operations:
  - search       : ejecuta una búsqueda (operación por defecto)
  - configure    : guarda api_key y defaults via Tool Config Protocol
  - show_config  : devuelve la configuración actual con api_key enmascarada

Primer consumidor del Tool Config Protocol (ver
``core/ports/outbound/tool_config_port.py``): las credenciales se persisten
en ``tool_config.web_search`` de ``config/tool_config.yaml`` — el usuario puede
pasarle la api_key al agente conversando, sin editar YAML a mano. La api_key
se cifra en reposo (``enc:``); el resto de campos quedan en plano.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.ports.outbound.tool_config_port import IToolConfigStore
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)

_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_DEFAULT_SEARCH_DEPTH = "basic"
_DEFAULT_MAX_RESULTS = 5
_HTTP_TIMEOUT = 20.0

_SENSITIVE_FIELDS: frozenset[str] = frozenset({"api_key"})


class WebSearchTool(ITool):
    name = "web_search"
    description = (
        "Search the internet for current, real-time, or external information via Tavily. "
        "Use this whenever the user asks about recent events, news, prices, weather, "
        "facts you are unsure about, or anything that may have changed or lies outside your context. "
        "Default: call with operation='search' and the 'query' parameter. "
        "If the call fails due to credentials, the error explains how to proceed. "
        "Other operations: "
        "'configure' with 'api_key' (optionally 'search_depth', 'max_results') to store Tavily credentials; "
        "'show_config' to inspect the current configuration."
    )
    # Disparadores multilingües solo para el embedding del semantic routing.
    routing_keywords = (
        "buscá en internet, busca en la web, googleá, qué noticias hay, qué pasó con, "
        "información actual, precio de, cotización, clima, tiempo, últimas noticias, "
        "buscar en línea, qué está pasando, novedades sobre. "
        "search the web, look it up online, google it, latest news, current price, "
        "what's happening with, recent events, find online, look up. "
        "cherche sur internet, recherche web, dernières nouvelles, prix actuel, quoi de neuf."
    )
    config_namespace = "web_search"
    parameters_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["search", "configure", "show_config"],
                "description": "Operación a ejecutar. Default: 'search'.",
                "default": "search",
            },
            "query": {
                "type": "string",
                "description": "Consulta de búsqueda (solo para operation=search).",
            },
            "max_results": {
                "type": "integer",
                "description": "Número máximo de resultados (default: 5).",
            },
            "search_depth": {
                "type": "string",
                "enum": ["basic", "advanced"],
                "description": "Profundidad de búsqueda de Tavily.",
            },
            "api_key": {
                "type": "string",
                "description": "API key de Tavily (solo para operation=configure).",
            },
        },
        "required": [],
    }

    def __init__(self, config_store: IToolConfigStore) -> None:
        self._store = config_store

    async def execute(self, **kwargs: Any) -> ToolResult:
        operation = str(kwargs.get("operation") or "search").strip().lower()
        try:
            if operation == "search":
                return await self._do_search(kwargs)
            if operation == "configure":
                return self._do_configure(kwargs)
            if operation == "show_config":
                return self._do_show_config()
            return self._error(
                f"Operación desconocida: '{operation}'. Usá 'search', 'configure' o 'show_config'."
            )
        except Exception as exc:
            logger.exception("WebSearchTool error")
            return self._error(f"Error interno: {exc}", error=str(exc))

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    async def _do_search(self, params: dict[str, Any]) -> ToolResult:
        query = str(params.get("query") or "").strip()
        if not query:
            return self._error("Falta el parámetro 'query'. No reintentes sin un query válido.")

        config = self._store.get(self.config_namespace)
        api_key = config.get("api_key")
        if not api_key:
            return self._error(
                "CONFIGURATION REQUIRED: Tavily api_key no configurada. "
                "NO REINTENTES esta búsqueda. En su lugar, pedile al usuario una API key de "
                "https://tavily.com y luego llamá a esta misma tool con "
                "operation='configure' y api_key=<la clave>."
            )

        max_results = int(
            params.get("max_results") or config.get("max_results") or _DEFAULT_MAX_RESULTS
        )
        search_depth = str(
            params.get("search_depth") or config.get("search_depth") or _DEFAULT_SEARCH_DEPTH
        ).lower()

        logger.info("WebSearchTool: '%s' (depth=%s, k=%d)", query, search_depth, max_results)

        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
        }

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(_TAVILY_ENDPOINT, json=payload)
        except httpx.HTTPError as exc:
            return self._error(
                f"Error de red contactando Tavily: {exc}. "
                "NO REINTENTES automáticamente — puede ser un problema transitorio pero "
                "el usuario debería saberlo.",
                error=str(exc),
            )

        if resp.status_code == 401:
            return self._error(
                "Tavily rechazó la api_key (401). NO REINTENTES. "
                "Pedile al usuario una api_key válida y re-configurá con operation=configure."
            )
        if resp.status_code == 429:
            return self._error(
                "Tavily rate limit (429). NO REINTENTES en este turno. "
                "Avisá al usuario del rate limit."
            )
        if resp.status_code >= 400:
            return self._error(
                f"Tavily devolvió HTTP {resp.status_code}: {resp.text[:200]}. "
                "NO REINTENTES automáticamente.",
                error=f"http_{resp.status_code}",
            )

        data = resp.json()
        results = data.get("results", [])
        answer = data.get("answer")

        if not results and not answer:
            return ToolResult(
                tool_name=self.name,
                output="No se encontraron resultados.",
                success=True,
            )

        lines: list[str] = []
        if answer:
            lines.append(f"Resumen Tavily: {answer}")
            lines.append("")
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')}")
            lines.append(f"   {r.get('url', '')}")
            snippet = (r.get("content") or "").strip()
            if snippet:
                lines.append(f"   {snippet}")
            lines.append("")

        return ToolResult(
            tool_name=self.name,
            output="\n".join(lines).strip(),
            success=True,
        )

    def _do_configure(self, params: dict[str, Any]) -> ToolResult:
        data: dict[str, Any] = {}

        api_key = str(params.get("api_key") or "").strip()
        if api_key:
            data["api_key"] = api_key

        search_depth = str(params.get("search_depth") or "").strip().lower()
        if search_depth:
            if search_depth not in ("basic", "advanced"):
                return self._error(
                    f"search_depth inválido: '{search_depth}'. Usá 'basic' o 'advanced'."
                )
            data["search_depth"] = search_depth

        max_results_raw = params.get("max_results")
        if max_results_raw not in (None, ""):
            try:
                # max_results_raw es Any | None acá; el filtro de arriba
                # descarta None y "" pero mypy no narrowea contra "".
                data["max_results"] = int(max_results_raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return self._error(
                    f"max_results inválido: '{max_results_raw}'. Debe ser un entero."
                )

        if not data:
            return self._error(
                "No se proveyeron campos. Al menos 'api_key' es requerido para la primera configuración."
            )

        self._store.set(self.config_namespace, data, sensitive=_SENSITIVE_FIELDS)

        return ToolResult(
            tool_name=self.name,
            output=(
                "Configuración de Tavily guardada correctamente. "
                f"Campos actualizados: {list(data.keys())}. "
                "Ya podés usar operation='search'."
            ),
            success=True,
        )

    def _do_show_config(self) -> ToolResult:
        masked = self._store.masked(self.config_namespace)
        if not masked:
            return ToolResult(
                tool_name=self.name,
                output=(
                    "No hay configuración de web_search. "
                    "Usá operation='configure' con api_key=<tu Tavily key> para configurar."
                ),
                success=True,
            )
        lines = [f"{k}: {v}" for k, v in masked.items()]
        return ToolResult(
            tool_name=self.name,
            output="Configuración actual:\n" + "\n".join(lines),
            success=True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _error(self, message: str, error: str | None = None) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            output=message,
            success=False,
            error=error or message,
        )
