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
            # El texto a embeber combina la descripción (lo que ve el LLM) con los
            # routing_keywords (disparadores multilingües solo para el retrieval).
            # Si routing_keywords está vacío, el texto es solo la descripción → 100%
            # backward-compat con tools que no definen keywords.
            routing_keywords = getattr(tool, "routing_keywords", "") or ""
            embed_text = (
                f"{tool.description}\n\n{routing_keywords}".strip()
                if routing_keywords
                else tool.description
            )
            # El hash incluye el texto combinado: si cambian description O keywords,
            # el cache se invalida y se recalcula el embedding.
            content_hash = hashlib.md5(embed_text.encode("utf-8")).hexdigest()

            embedding: list[float] | None = None
            if self._cache is not None:
                embedding = await self._cache.get(
                    content_hash, self._provider_name, self._dimension
                )

            if embedding is None:
                embedding = await self._embedder.embed_passage(embed_text)
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
        tool = self._tools[tool_name]

        # Validar argumentos obligatorios ANTES de invocar. El LLM a veces emite
        # tool calls incompletas (caso real: `delegate` sin `agent_id`). Sin esta
        # guarda, `tool.execute(**kwargs)` revienta con un TypeError críptico
        # ("missing 1 required positional argument: 'agent_id'") que el modelo no
        # sabe interpretar ni corregir. Validamos contra el `required` del schema
        # —el contrato que le advertimos al LLM— y devolvemos un error claro y
        # RETRYABLE para que reintente con los campos que faltan, en vez de
        # tripear el circuit breaker con un fallo que el modelo puede arreglar.
        faltantes = _missing_required_args(tool, kwargs)
        if faltantes:
            logger.warning(
                "Tool '%s' invocada sin argumentos obligatorios %s (recibidos: %s)",
                tool_name,
                faltantes,
                list(kwargs.keys()),
            )
            return ToolResult(
                tool_name=tool_name,
                output=(
                    f"Faltan argumentos obligatorios para '{tool_name}': "
                    f"{', '.join(faltantes)}. Argumentos esperados: "
                    f"{_format_expected_args(tool)}. Reintentá la llamada "
                    f"incluyendo {', '.join(faltantes)}."
                ),
                success=False,
                error=f"missing_required_args: {', '.join(faltantes)}",
                retryable=True,
            )

        try:
            return await tool.execute(**kwargs)
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


def _missing_required_args(tool: ITool, kwargs: dict) -> list[str]:
    """Devuelve los parámetros ``required`` del schema que no están en ``kwargs``.

    Fuente de verdad: ``tool.parameters_schema["required"]`` — el contrato que
    se le expone al LLM. Robusto ante schemas sin ``required`` o malformados.
    """
    schema = getattr(tool, "parameters_schema", None) or {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        return []
    return [name for name in required if name not in kwargs]


def _format_expected_args(tool: ITool) -> str:
    """Formatea los parámetros del schema como ``nombre (obligatorio|opcional)``.

    Le da al LLM el contrato completo de la tool en el mensaje de error, para
    que sepa exactamente qué reenviar.
    """
    schema = getattr(tool, "parameters_schema", None) or {}
    props = schema.get("properties", {})
    if not isinstance(props, dict) or not props:
        return "(sin parámetros declarados)"
    required = set(schema.get("required", []) or [])
    return ", ".join(
        f"{name} ({'obligatorio' if name in required else 'opcional'})" for name in props
    )
