"""WebSearchTool — búsqueda web via Tavily API.

Operations:
  - search       : ejecuta una búsqueda (operación por defecto)
  - configure    : guarda api_key y defaults en ~/.inaki/config/web_search_config.yaml
  - show_config  : devuelve la configuración actual con api_key enmascarada

La api_key se cifra en disco con CryptoService (Fernet).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from adapters.outbound.tools.web_search_config_store import WebSearchConfigStore
from core.ports.outbound.tool_port import ITool, ToolResult
from core.services.crypto_service import CryptoService

logger = logging.getLogger(__name__)

_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_DEFAULT_SEARCH_DEPTH = "basic"
_DEFAULT_MAX_RESULTS = 5
_HTTP_TIMEOUT = 20.0


class WebSearchTool(ITool):
    name = "web_search"
    description = (
        "Busca información en internet vía Tavily. "
        "Usar para eventos actuales, datos volátiles o info fuera del contexto. "
        "Default: llamá con operation='search' y el parámetro 'query'. "
        "Si la llamada falla por credenciales, el error te indica cómo proceder. "
        "Otras operaciones: "
        "'configure' con 'api_key' (y opcionalmente 'search_depth', 'max_results') para guardar credenciales de Tavily; "
        "'show_config' para inspeccionar la configuración actual."
    )
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

    def __init__(self) -> None:
        self._store = WebSearchConfigStore(CryptoService())

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
                f"Operación desconocida: '{operation}'. "
                "Usá 'search', 'configure' o 'show_config'."
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
            return self._error(
                "Falta el parámetro 'query'. No reintentes sin un query válido."
            )

        config = self._store.load()
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
                data["max_results"] = int(max_results_raw)
            except (TypeError, ValueError):
                return self._error(
                    f"max_results inválido: '{max_results_raw}'. Debe ser un entero."
                )

        if not data:
            return self._error(
                "No se proveyeron campos. Al menos 'api_key' es requerido para la primera configuración."
            )

        self._store.save(data)

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
        masked = self._store.masked()
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
