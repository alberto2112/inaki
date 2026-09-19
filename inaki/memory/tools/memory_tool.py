"""
MemoryTool — tool única de memoria a largo plazo expuesta al LLM.

Reemplaza a la tríada ``search_memory``/``delete_memory``/``update_memory``
(retiradas en la fase 4 del plan `memory-tool-unificada`)
con una sola tool ``memory`` de discriminador ``operation``, misma convención
que ``scheduler`` y ``knowledge_admin``.

Operaciones:
  - ``search``: búsqueda semántica sobre la memoria del agente (lógica de
    la antigua ``search_memory``, tal cual).
  - ``list``: los recuerdos más recientes del SCOPE ACTUAL del turno — lo
    mismo que ve el digest, pero con ids (para poder pedir un ``update``/
    ``delete`` sin buscar antes).
  - ``update`` / ``delete``: gestión de un recuerdo existente por id (lógica
    de las antiguas ``update_memory``/``delete_memory``, tal cual). Regeneran el
    digest del scope de la entry afectada.
  - ``create`` (SOLO si ``capture.enabled``): captura en vivo. Un solo
    ``embed_passage`` sirve para dedup (``search_with_scores`` contra ese
    mismo vector) y para persistir. El candidato a fusión es el PRIMER
    vecino (de los ``_DEDUP_TOP_K``, ya ordenados por score desc) cuyo
    ``agent_id`` coincide con el propio — ``search_with_scores`` no filtra
    por agente ni scope, así que un vecino de OTRO agente o un recuerdo
    "global" (``agent_id=None``) nunca es candidato, aunque rankee más
    alto (ver comentario en ``_create``). Si ESE candidato supera
    ``capture.dedup_similarity`` se fusiona (``update`` del vecino); si no,
    se crea una entry nueva. Nunca en silencio: la salida siempre dice si
    creó o fusionó, y con qué id. Regenera el digest del scope al final.

Scope del turno: ``get_channel_context()`` (patrón idéntico a
``SchedulerTool`` — callable inyectado por el composition root, ``None`` en
triggers del scheduler y en tests). Con contexto: ``channel =
ctx.channel_type``, ``chat_id = ctx.chat_id or ""`` — mismo criterio que
``RunAgentUseCase`` al perfilar mensajes, así el scope de un ``create`` en
vivo coincide con el que usa la consolidación nocturna para el mismo turno.
Sin contexto (scheduler, tests): ``channel = None``, ``chat_id = None`` — el
recuerdo "global" que ya existe como concepto (filas pre-migración).

Invariante ``outbound-send-single-owner``: esta tool NUNCA escribe en
``history.db`` ni recibe el history store — el tool loop persiste el rastro
protocolar, no la tool.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from inaki.kernel.domain.agent_settings import CaptureSettings
from inaki.kernel.domain.memory import MemoryEntry
from inaki.kernel.ports.embedding_port import IEmbeddingProvider
from inaki.kernel.ports.memory_port import IMemoryRepository
from inaki.kernel.ports.tool_port import ITool, ToolResult
from inaki.memory.policy import MEMORY_POLICY
from inaki.memory.use_cases.digest import DigestWriter
from inaki.shared.channel_context import ChannelContext

logger = logging.getLogger(__name__)


_DEFAULT_TOP_K = 5
_DEFAULT_LIST_TOP_K = 10
_MAX_TOP_K = 20
_DEFAULT_CREATE_RELEVANCE = 0.8
# Vecinos a traer para el dedup de `create`. Más de 1 porque
# `search_with_scores` no filtra por agent_id (ver `_create`) y el mejor
# rankeado puede ser de otro agente — necesitamos margen para encontrar,
# entre los top-k, el primero que sea del agente propio.
_DEDUP_TOP_K = 5

# Operaciones siempre disponibles (gestionan datos existentes; inofensivas
# con la DB vacía). `create` se agrega al enum/schema solo si capture.enabled.
_OPERACIONES_BASE = ("search", "list", "update", "delete")


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


class MemoryTool(ITool):
    name = "memory"
    # `description` se arma dinámicamente en __init__ porque depende de si
    # `capture.enabled` habilita la operación `create` — ver _build_description.
    description = ""
    routing_keywords = (
        # search/list (recall) — de la tool search_memory original.
        "te acordás, qué te dije sobre, qué guardé, qué recordás de mí, mis datos, "
        "lo que hablamos, qué sabés sobre mí, mis preferencias, recordá lo que. "
        "do you remember, what did I tell you about, what do you know about me, "
        "my preferences, what we discussed, recall about me. "
        "tu te souviens, qu'est-ce que je t'ai dit, mes préférences, ce qu'on a dit. "
        # create/update/delete (captura y edición) — nuevos, multilingües es/en/fr.
        "acordate de esto, recordá que, guardá esto, guardate este dato, "
        "olvidate de, borrá ese recuerdo, borrá esa memoria, corregí el recuerdo, "
        "actualizá ese recuerdo. "
        "remember that, save this, keep in mind that, forget that, delete that memory, "
        "correct that memory, update that memory. "
        "souviens-toi que, retiens que, oublie ça, supprime ce souvenir, corrige ce souvenir."
    )

    def __init__(
        self,
        *,
        memory: IMemoryRepository,
        embedder: IEmbeddingProvider,
        agent_id: str,
        get_channel_context: Callable[[], ChannelContext | None],
        digest: DigestWriter,
        capture: CaptureSettings,
    ) -> None:
        self._memory = memory
        self._embedder = embedder
        self._agent_id = agent_id
        self._get_channel_context = get_channel_context
        self._digest = digest
        self._capture = capture

        operaciones = list(_OPERACIONES_BASE)
        if capture.enabled:
            operaciones.append("create")
        self._operaciones_validas = tuple(operaciones)

        self.description = _build_description(capture.enabled)
        self.parameters_schema = _build_parameters_schema(self._operaciones_validas)

    # -- scope del turno ----------------------------------------------------

    def _scope_actual(self) -> tuple[str | None, str | None]:
        """Resuelve ``(channel, chat_id)`` del turno en curso.

        Con contexto: mismo criterio que ``RunAgentUseCase`` (``chat_id or
        ""``, nunca ``None`` si hay contexto) para que el scope de un
        ``create`` en vivo coincida con el de la consolidación. Sin contexto
        (scheduler, tests): ``(None, None)`` — el recuerdo "global".
        """
        ctx = self._get_channel_context()
        if ctx is None:
            return None, None
        return ctx.channel_type, ctx.chat_id or ""

    # -- despacho -------------------------------------------------------

    async def execute(self, **kwargs) -> ToolResult:
        operation = str(kwargs.get("operation") or "").strip().lower()

        # `create` con capture apagado tiene un mensaje PROPIO que nombra el
        # flag — no el genérico "unknown operation" (que además ni siquiera
        # listaría 'create' entre las válidas). Chequeo explícito ANTES del
        # despacho por tabla para no perder esa distinción.
        if operation == "create" and not self._capture.enabled:
            return self._fail(
                "Operation 'create' is disabled: set 'memories.capture.enabled: true' "
                "in the agent config to enable live memory capture."
            )

        if operation not in self._operaciones_validas:
            return self._fail(
                f"Unknown operation '{operation}'. Valid operations: "
                f"{', '.join(self._operaciones_validas)}."
            )

        try:
            if operation == "search":
                return await self._search(kwargs)
            if operation == "list":
                return await self._list(kwargs)
            if operation == "update":
                return await self._update(kwargs)
            if operation == "delete":
                return await self._delete(kwargs)
            return await self._create(kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("MemoryTool: error inesperado (operation=%s)", operation)
            # Excepción no prevista del repo/embedder — típicamente transitoria
            # (lock de DB, timeout de red). retryable=True, igual que las tools
            # viejas: un `except Exception` acá no es un error de validación de
            # parámetros (esos usan `_fail(..., retryable=False)` explícito más
            # arriba), y contarlo como no-retryable dispara antes el circuit
            # breaker del tool loop (`inaki/kernel/_tool_loop.py`).
            return self._fail(f"Internal error: {exc}", retryable=True)

    def _fail(self, message: str, *, retryable: bool = False) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            output=message,
            success=False,
            error=message,
            retryable=retryable,
        )

    def _ok(self, output: str) -> ToolResult:
        return ToolResult(tool_name=self.name, output=output, success=True)

    # -- search ---------------------------------------------------------

    async def _search(self, kwargs: dict) -> ToolResult:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return self._fail("The 'query' parameter is required for operation 'search'.")

        top_k = _clamp_top_k(kwargs.get("top_k"), default=_DEFAULT_TOP_K)

        query_vec = await self._embedder.embed_query(query)
        scored = await self._memory.search_with_scores(query_vec, top_k=top_k)

        if not scored:
            return self._ok("No memories matched that query.")

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
        return self._ok("\n".join(lines))

    # -- list -------------------------------------------------------------

    async def _list(self, kwargs: dict) -> ToolResult:
        top_k = _clamp_top_k(kwargs.get("top_k"), default=_DEFAULT_LIST_TOP_K)
        channel, chat_id = self._scope_actual()

        entries = await self._memory.get_recent(
            top_k,
            agent_id=self._agent_id,
            channel=channel,
            chat_id=chat_id,
        )

        if not entries:
            return self._ok("No memories recorded for this scope yet.")

        lines = [f"{len(entries)} memory entries for this scope:"]
        for entry in entries:
            lines.append(
                _format_entry_summary(
                    memory_id=entry.id,
                    content=entry.content,
                    relevance=entry.relevance,
                    tags=entry.tags,
                    created_at=entry.created_at.isoformat(),
                    channel=entry.channel,
                    chat_id=entry.chat_id,
                )
            )
        return self._ok("\n".join(lines))

    # -- update -------------------------------------------------------------

    async def _update(self, kwargs: dict) -> ToolResult:
        memory_id = str(kwargs.get("memory_id") or "").strip()
        if not memory_id:
            return self._fail("The 'memory_id' parameter is required for operation 'update'.")

        content_raw = kwargs.get("content")
        tags_raw = kwargs.get("tags")
        relevance_raw = kwargs.get("relevance")

        if content_raw is None and tags_raw is None and relevance_raw is None:
            return self._fail(
                "At least one of 'content', 'tags' or 'relevance' must be provided "
                "for operation 'update'."
            )

        content: str | None = None
        if content_raw is not None:
            content = str(content_raw).strip()
            if not content:
                return self._fail("'content' cannot be empty.")

        tags: list[str] | None = None
        if tags_raw is not None:
            if not isinstance(tags_raw, list):
                return self._fail("'tags' must be a list of strings.")
            tags = [str(t) for t in tags_raw]

        relevance: float | None = None
        if relevance_raw is not None:
            try:
                relevance = float(relevance_raw)
            except (TypeError, ValueError):
                return self._fail("'relevance' must be a number between 0.0 and 1.0.")
            if not (0.0 <= relevance <= 1.0):
                return self._fail("'relevance' must be between 0.0 and 1.0.")

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

        if entry is None:
            return self._fail(
                f"No active memory with id '{memory_id}' (deleted or never existed). "
                "Update aborted."
            )

        await self._digest.write(entry.channel, entry.chat_id)

        return self._ok(
            f"Updated memory id={entry.id}\n"
            f"  content: {entry.content}\n"
            f"  relevance: {entry.relevance:.2f}\n"
            f"  tags: {json.dumps(entry.tags, ensure_ascii=False)}\n"
            f"  scope: ({entry.channel or '-'}, {entry.chat_id or '-'})"
        )

    # -- delete -------------------------------------------------------------

    async def _delete(self, kwargs: dict) -> ToolResult:
        memory_id = str(kwargs.get("memory_id") or "").strip()
        if not memory_id:
            return self._fail("The 'memory_id' parameter is required for operation 'delete'.")

        entry = await self._memory.delete(memory_id)

        if entry is None:
            existing = await self._memory.get_by_id(memory_id)
            if existing is None:
                return self._fail(
                    f"Memory id '{memory_id}' not found in database. Make sure to call "
                    "operation='search' or operation='list' first and use the exact UUID "
                    "from those results — never invent or reuse IDs from other sources."
                )
            return self._ok(f"Memory id '{memory_id}' was already deleted. No-op.")

        await self._digest.write(entry.channel, entry.chat_id)

        return self._ok(
            f"Deleted memory id={entry.id}\n"
            f"  content: {entry.content}\n"
            f"  scope: ({entry.channel or '-'}, {entry.chat_id or '-'})"
        )

    # -- create ---------------------------------------------------------

    async def _create(self, kwargs: dict) -> ToolResult:
        content = str(kwargs.get("content") or "").strip()
        if not content:
            return self._fail("The 'content' parameter is required for operation 'create'.")

        tags_raw = kwargs.get("tags")
        if tags_raw is None:
            tags: list[str] = []
        elif isinstance(tags_raw, list):
            tags = [str(t) for t in tags_raw]
        else:
            return self._fail("'tags' must be a list of strings.")

        relevance_raw = kwargs.get("relevance")
        if relevance_raw is None:
            relevance = _DEFAULT_CREATE_RELEVANCE
        else:
            try:
                relevance = float(relevance_raw)
            except (TypeError, ValueError):
                return self._fail("'relevance' must be a number between 0.0 and 1.0.")
        if not (0.0 <= relevance <= 1.0):
            return self._fail("'relevance' must be between 0.0 and 1.0.")

        channel, chat_id = self._scope_actual()

        # Una sola llamada al embedder: sirve para el dedup (search_with_scores)
        # Y para persistir (store/update), sea cual sea el desenlace.
        embedding = await self._embedder.embed_passage(content)

        # `search_with_scores` NO filtra por agent_id (el WHERE del adapter
        # SQLite solo excluye deleted=1) ni por scope — el store por default
        # es COMPARTIDO por todos los agentes (aislamiento por columna, no
        # por fichero). El vecino mejor rankeado puede ser de OTRO agente: si
        # lo tomáramos tal cual, un `create` de este agente podría hacer
        # `update` sobre la memoria de otro. Mismo enfoque que
        # ``ReconcileMemoryUseCase`` (filtra el cluster a mano, ver
        # `reconcile_memory.py`): recorremos los vecinos en orden de score
        # (ya vienen desc) y tomamos el PRIMERO cuyo `agent_id` sea el
        # nuestro. Un recuerdo "global" (`agent_id=None`, compartido a
        # propósito) NUNCA es candidato a fusión — solo se fusiona contra
        # memoria del propio agente.
        neighbors = await self._memory.search_with_scores(embedding, top_k=_DEDUP_TOP_K)
        candidate = next(
            ((entry, score) for entry, score in neighbors if entry.agent_id == self._agent_id),
            None,
        )
        if candidate is not None:
            neighbor, score = candidate
            if score >= self._capture.dedup_similarity:
                merged_tags = list(neighbor.tags)
                for t in tags:
                    if t not in merged_tags:
                        merged_tags.append(t)
                merged_relevance = max(neighbor.relevance, relevance)

                updated = await self._memory.update(
                    neighbor.id,
                    content=content,
                    tags=merged_tags,
                    relevance=merged_relevance,
                    embedding=embedding,
                )
                if updated is None:
                    # `neighbor` venía de search_with_scores (activo, no
                    # deleted): esto solo pasa ante una carrera externa (otro
                    # caller borró el recuerdo entre el search y el update).
                    return self._fail(
                        f"Dedup target id={neighbor.id} was deleted concurrently; "
                        "retry the create.",
                        retryable=True,
                    )
                await self._digest.write(updated.channel, updated.chat_id)
                return self._ok(
                    f"Merged into existing memory id={updated.id} (similarity={score:.3f})\n"
                    f"  previous: {neighbor.content}\n"
                    f"  now: {updated.content}\n"
                    f"  scope: ({updated.channel or '-'}, {updated.chat_id or '-'})"
                )

        entry = MemoryEntry(
            content=content,
            embedding=embedding,
            relevance=relevance,
            tags=tags,
            agent_id=self._agent_id,
            channel=channel,
            chat_id=chat_id,
        )
        await self._memory.store(entry)
        await self._digest.write(channel, chat_id)

        return self._ok(
            f"Created memory id={entry.id} scope=({channel or '-'}, {chat_id or '-'})\n"
            f"  content: {entry.content}"
        )


def _clamp_top_k(raw: Any, *, default: int) -> int:
    top_k = int(raw) if raw is not None else default
    return max(1, min(top_k, _MAX_TOP_K))


def _build_description(capture_enabled: bool) -> str:
    base = (
        "Manage the user's long-term memory for this agent — distilled facts, "
        "preferences, and events about the user, separate from raw conversation "
        "history. Operations: search, list, update, delete"
        + (", create" if capture_enabled else "")
        + ". "
        "Use 'search' to find memories by semantic similarity (needs 'query'). "
        "Use 'list' to see the most recent memories for the CURRENT conversation's "
        "scope (no query needed) — this is what the digest shows you. "
        "Both 'search' and 'list' return each entry's real UUID 'id' — you need it "
        "for 'update' and 'delete'. Never invent or guess a UUID. "
        "Use 'update' to edit an existing memory's content, tags, or relevance "
        "(needs 'memory_id' and at least one field to change). "
        "Use 'delete' to soft-delete a memory by its 'memory_id'. "
    )
    if capture_enabled:
        base += (
            "Use 'create' to save a new memory in your own words as soon as the "
            "conversation reveals something worth remembering long-term (needs "
            "'content'; optional 'tags' and 'relevance', default 0.8). Apply this "
            "policy when deciding what to save:\n\n"
            f"{MEMORY_POLICY}\n"
            "'create' automatically deduplicates: if the new content is very "
            "similar to an existing memory, it MERGES into that memory instead of "
            "creating a duplicate — the result tells you whether it created a new "
            "entry or merged into an existing one, and with which id. "
        )
    base += (
        "This tool is for DISTILLED facts about the user, not verbatim text — for "
        "the literal text of a past conversation use `search_history`, and for "
        "documents or knowledge bases use `knowledge_search`."
    )
    return base


def _build_parameters_schema(operaciones_validas: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(operaciones_validas),
                "description": "Operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Semantic search query (required for 'search').",
            },
            "top_k": {
                "type": "integer",
                "description": (
                    "Maximum number of results for 'search' (default 5) or 'list' "
                    "(default 10). Capped at 20."
                ),
            },
            "memory_id": {
                "type": "string",
                "description": (
                    "UUID of the memory entry to act on (required for 'update' and "
                    "'delete'). Obtain it from a previous 'search' or 'list' result — "
                    "never invent one."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "For 'create': the memory text to save (required). For 'update': "
                    "new content that replaces the existing one and triggers embedding "
                    "recomputation (optional)."
                ),
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "For 'create': optional tags for the new memory. For 'update': "
                    "replaces the existing tags list."
                ),
            },
            "relevance": {
                "type": "number",
                "description": (
                    "Confidence (0.0-1.0) that this memory is worth keeping. For "
                    "'create', optional, default 0.8. For 'update', optional new value."
                ),
            },
        },
        "required": ["operation"],
    }
