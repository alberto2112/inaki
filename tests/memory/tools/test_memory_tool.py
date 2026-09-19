"""Tests para la tool única ``memory`` (fases 3-4 del plan `memory-tool-unificada`).

El fichero viejo (``tests/memory/tools/test_memory_tools.py``, tres tools
``search_memory``/``delete_memory``/``update_memory``) se borró en la fase 4
junto con ``inaki/memory/tools/memory_tools.py`` — esta tool única los reemplaza.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from inaki.kernel.domain.agent_settings import CaptureSettings
from inaki.kernel.domain.memory import MemoryEntry
from inaki.memory.policy import MEMORY_POLICY
from inaki.memory.tools.memory_tool import MemoryTool
from inaki.shared.channel_context import ChannelContext


def _entry(
    content: str,
    *,
    memory_id: str = "abc-1234",
    deleted: bool = False,
    relevance: float = 0.9,
    tags: list[str] | None = None,
    channel: str | None = "telegram",
    chat_id: str | None = "-1001",
    agent_id: str | None = "test",
) -> MemoryEntry:
    return MemoryEntry(
        id=memory_id,
        content=content,
        embedding=[0.1] * 384,
        relevance=relevance,
        tags=tags if tags is not None else ["python"],
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        agent_id=agent_id,
        channel=channel,
        chat_id=chat_id,
        deleted=deleted,
    )


def _make_tool(
    *,
    memory=None,
    embedder=None,
    agent_id: str = "test",
    get_channel_context=None,
    digest=None,
    capture: CaptureSettings | None = None,
) -> MemoryTool:
    return MemoryTool(
        memory=memory,
        embedder=embedder,
        agent_id=agent_id,
        get_channel_context=get_channel_context or (lambda: None),
        digest=digest if digest is not None else AsyncMock(),
        capture=capture if capture is not None else CaptureSettings(),
    )


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def test_search_returns_id_in_output(mock_memory, mock_embedder):
    entry = _entry("le gusta Python")
    mock_memory.search_with_scores.return_value = [(entry, 0.85)]
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="search", query="python")

    assert result.success is True
    assert "abc-1234" in result.output
    assert "le gusta Python" in result.output
    assert "score=0.850" in result.output


async def test_search_empty_query_fails(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="search", query="   ")

    assert result.success is False
    assert "query" in result.output.lower()


async def test_search_no_results(mock_memory, mock_embedder):
    mock_memory.search_with_scores.return_value = []
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="search", query="algo")

    assert result.success is True
    assert "No memories matched" in result.output


async def test_search_caps_top_k_at_max(mock_memory, mock_embedder):
    mock_memory.search_with_scores.return_value = []
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    await tool.execute(operation="search", query="x", top_k=999)

    call_kwargs = mock_memory.search_with_scores.call_args.kwargs
    assert call_kwargs["top_k"] == 20


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def test_list_uses_turn_scope(mock_memory, mock_embedder):
    mock_memory.get_recent.return_value = [_entry("recuerdo 1")]
    ctx = ChannelContext(channel_type="telegram", user_id="99", chat_id="-500")
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, get_channel_context=lambda: ctx)

    result = await tool.execute(operation="list")

    assert result.success is True
    assert "abc-1234" in result.output
    mock_memory.get_recent.assert_awaited_once_with(
        10, agent_id="test", channel="telegram", chat_id="-500"
    )


async def test_list_without_context_uses_none_none_scope(mock_memory, mock_embedder):
    mock_memory.get_recent.return_value = []
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, get_channel_context=lambda: None)

    result = await tool.execute(operation="list")

    assert result.success is True
    mock_memory.get_recent.assert_awaited_once_with(10, agent_id="test", channel=None, chat_id=None)


async def test_list_empty_scope_returns_success_empty(mock_memory, mock_embedder):
    mock_memory.get_recent.return_value = []
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="list")

    assert result.success is True
    assert "No memories recorded" in result.output


async def test_list_caps_top_k_at_max(mock_memory, mock_embedder):
    mock_memory.get_recent.return_value = []
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    await tool.execute(operation="list", top_k=999)

    call_args = mock_memory.get_recent.call_args
    assert call_args.args[0] == 20


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


async def test_update_content_recomputes_embedding_and_regenerates_digest(
    mock_memory, mock_embedder
):
    updated = _entry("nuevo contenido", channel="telegram", chat_id="-1001")
    mock_memory.update.return_value = updated
    mock_embedder.embed_passage.return_value = [0.5] * 384
    digest = AsyncMock()
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, digest=digest)

    result = await tool.execute(operation="update", memory_id="abc-1234", content="nuevo contenido")

    assert result.success is True
    mock_embedder.embed_passage.assert_awaited_once_with("nuevo contenido")
    update_kwargs = mock_memory.update.call_args.kwargs
    assert update_kwargs["content"] == "nuevo contenido"
    assert update_kwargs["embedding"] == [0.5] * 384
    digest.write.assert_awaited_once_with("telegram", "-1001")


async def test_update_tags_only_does_not_touch_embedding(mock_memory, mock_embedder):
    mock_memory.update.return_value = _entry("igual")
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    await tool.execute(operation="update", memory_id="abc", tags=["uno", "dos"])

    mock_embedder.embed_passage.assert_not_awaited()
    update_kwargs = mock_memory.update.call_args.kwargs
    assert update_kwargs["embedding"] is None
    assert update_kwargs["tags"] == ["uno", "dos"]


async def test_update_no_fields_provided_fails(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="update", memory_id="abc")

    assert result.success is False
    mock_memory.update.assert_not_awaited()


async def test_update_invalid_relevance_fails(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="update", memory_id="abc", relevance=2.5)

    assert result.success is False
    assert "0.0 and 1.0" in result.output
    mock_memory.update.assert_not_awaited()


async def test_update_tags_must_be_list(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="update", memory_id="abc", tags="no-soy-lista")

    assert result.success is False
    mock_memory.update.assert_not_awaited()


async def test_update_empty_content_fails(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="update", memory_id="abc", content="   ")

    assert result.success is False
    mock_memory.update.assert_not_awaited()


async def test_update_id_not_found_fails_and_no_digest(mock_memory, mock_embedder):
    mock_memory.update.return_value = None
    digest = AsyncMock()
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, digest=digest)

    result = await tool.execute(operation="update", memory_id="ghost", content="nuevo")

    assert result.success is False
    assert "ghost" in result.output
    digest.write.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_happy_path_regenerates_digest(mock_memory, mock_embedder):
    entry = _entry("borradito", deleted=True, channel="telegram", chat_id="-1001")
    mock_memory.delete.return_value = entry
    digest = AsyncMock()
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, digest=digest)

    result = await tool.execute(operation="delete", memory_id="abc-1234")

    assert result.success is True
    mock_memory.delete.assert_awaited_once_with("abc-1234")
    assert "abc-1234" in result.output
    assert "borradito" in result.output
    digest.write.assert_awaited_once_with("telegram", "-1001")


async def test_delete_already_deleted_is_success_no_op(mock_memory, mock_embedder):
    already_deleted = _entry("borrada", deleted=True)
    mock_memory.delete.return_value = None
    mock_memory.get_by_id.return_value = already_deleted
    digest = AsyncMock()
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, digest=digest)

    result = await tool.execute(operation="delete", memory_id="ghost")

    assert result.success is True
    assert "already deleted" in result.output.lower()
    digest.write.assert_not_awaited()


async def test_delete_id_not_in_db_returns_error(mock_memory, mock_embedder):
    mock_memory.delete.return_value = None
    mock_memory.get_by_id.return_value = None
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="delete", memory_id="ghost")

    assert result.success is False
    assert "not found" in result.output.lower()


async def test_delete_empty_id_fails(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="delete", memory_id="")

    assert result.success is False
    mock_memory.delete.assert_not_awaited()


async def test_delete_repo_exception_returned_as_error(mock_memory, mock_embedder):
    mock_memory.delete.side_effect = RuntimeError("DB lock")
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="delete", memory_id="abc")

    assert result.success is False
    assert "DB lock" in result.output
    # Excepción transitoria del repo (lock, timeout) — retryable=True, igual
    # que las tools viejas. No es un error de validación de parámetros.
    assert result.retryable is True


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def test_create_with_turn_scope(mock_memory, mock_embedder):
    mock_memory.search_with_scores.return_value = []
    ctx = ChannelContext(channel_type="telegram", user_id="99", chat_id="-500")
    digest = AsyncMock()
    tool = _make_tool(
        memory=mock_memory, embedder=mock_embedder, get_channel_context=lambda: ctx, digest=digest
    )

    result = await tool.execute(operation="create", content="le gusta el mate")

    assert result.success is True
    assert "Created memory id=" in result.output
    mock_memory.store.assert_awaited_once()
    stored: MemoryEntry = mock_memory.store.call_args.args[0]
    assert stored.channel == "telegram"
    assert stored.chat_id == "-500"
    assert stored.content == "le gusta el mate"
    assert stored.relevance == 0.8
    digest.write.assert_awaited_once_with("telegram", "-500")


async def test_create_without_context_creates_global_scope(mock_memory, mock_embedder):
    mock_memory.search_with_scores.return_value = []
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, get_channel_context=lambda: None)

    result = await tool.execute(operation="create", content="dato global")

    assert result.success is True
    stored: MemoryEntry = mock_memory.store.call_args.args[0]
    assert stored.channel is None
    assert stored.chat_id is None


async def test_create_merges_above_threshold(mock_memory, mock_embedder):
    neighbor = _entry("le gusta el café", relevance=0.6, tags=["bebidas"])
    mock_memory.search_with_scores.return_value = [(neighbor, 0.9)]
    merged = _entry("le encanta el café con leche", relevance=0.8, tags=["bebidas", "mañana"])
    mock_memory.update.return_value = merged
    digest = AsyncMock()
    capture = CaptureSettings(enabled=True, dedup_similarity=0.80)
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, digest=digest, capture=capture)

    result = await tool.execute(
        operation="create", content="le encanta el café con leche", tags=["mañana"]
    )

    assert result.success is True
    assert "Merged into existing memory id=abc-1234" in result.output
    assert "similarity=0.900" in result.output
    mock_memory.store.assert_not_awaited()
    update_kwargs = mock_memory.update.call_args.kwargs
    assert update_kwargs["content"] == "le encanta el café con leche"
    assert set(update_kwargs["tags"]) == {"bebidas", "mañana"}
    assert update_kwargs["relevance"] == pytest.approx(0.8)
    digest.write.assert_awaited_once()


async def test_create_does_not_merge_below_threshold(mock_memory, mock_embedder):
    neighbor = _entry("algo parecido")
    mock_memory.search_with_scores.return_value = [(neighbor, 0.5)]
    capture = CaptureSettings(enabled=True, dedup_similarity=0.80)
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, capture=capture)

    result = await tool.execute(operation="create", content="dato nuevo y distinto")

    assert result.success is True
    assert "Created memory id=" in result.output
    mock_memory.store.assert_awaited_once()
    mock_memory.update.assert_not_awaited()


async def test_create_dedup_skips_neighbor_from_other_agent(mock_memory, mock_embedder):
    """El vecino mejor rankeado (0.95) es de OTRO agente: no es candidato,
    aunque supere el umbral. El segundo (0.90, agente propio) SÍ lo es."""
    other_agent_neighbor = _entry(
        "recuerdo de otro agente", memory_id="other-agent-id", agent_id="anacleto"
    )
    own_agent_neighbor = _entry(
        "recuerdo propio parecido", memory_id="own-agent-id", agent_id="test"
    )
    mock_memory.search_with_scores.return_value = [
        (other_agent_neighbor, 0.95),
        (own_agent_neighbor, 0.90),
    ]
    merged = _entry("dato nuevo parecido", memory_id="own-agent-id", agent_id="test")
    mock_memory.update.return_value = merged
    capture = CaptureSettings(enabled=True, dedup_similarity=0.80)
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, agent_id="test", capture=capture)

    result = await tool.execute(operation="create", content="dato nuevo parecido")

    assert result.success is True
    assert "Merged into existing memory id=own-agent-id" in result.output
    assert "similarity=0.900" in result.output
    mock_memory.store.assert_not_awaited()
    mock_memory.update.assert_awaited_once()
    assert mock_memory.update.call_args.args[0] == "own-agent-id"


async def test_create_no_own_agent_neighbor_over_threshold_creates_new(mock_memory, mock_embedder):
    """El único vecino sobre el umbral es de otro agente → crea, no fusiona."""
    other_agent_neighbor = _entry(
        "recuerdo de otro agente", memory_id="other-agent-id", agent_id="anacleto"
    )
    mock_memory.search_with_scores.return_value = [(other_agent_neighbor, 0.95)]
    capture = CaptureSettings(enabled=True, dedup_similarity=0.80)
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, agent_id="test", capture=capture)

    result = await tool.execute(operation="create", content="dato nuevo y distinto")

    assert result.success is True
    assert "Created memory id=" in result.output
    mock_memory.store.assert_awaited_once()
    mock_memory.update.assert_not_awaited()


async def test_create_global_neighbor_over_threshold_is_never_a_merge_candidate(
    mock_memory, mock_embedder
):
    """Vecino con agent_id=None (global, compartido a propósito) sobre el
    umbral → NUNCA es candidato a fusión, aunque no sea de otro agente
    "concreto"."""
    global_neighbor = _entry("recuerdo global", memory_id="global-id", agent_id=None)
    mock_memory.search_with_scores.return_value = [(global_neighbor, 0.95)]
    capture = CaptureSettings(enabled=True, dedup_similarity=0.80)
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, agent_id="test", capture=capture)

    result = await tool.execute(operation="create", content="dato nuevo y distinto")

    assert result.success is True
    assert "Created memory id=" in result.output
    mock_memory.store.assert_awaited_once()
    mock_memory.update.assert_not_awaited()


async def test_create_calls_embed_passage_exactly_once(mock_memory, mock_embedder):
    mock_memory.search_with_scores.return_value = []
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    await tool.execute(operation="create", content="algo")

    mock_embedder.embed_passage.assert_awaited_once_with("algo")
    mock_embedder.embed_query.assert_not_awaited()


async def test_create_empty_content_fails(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="create", content="   ")

    assert result.success is False
    mock_embedder.embed_passage.assert_not_awaited()
    mock_memory.store.assert_not_awaited()


async def test_create_invalid_relevance_fails(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="create", content="algo", relevance=1.5)

    assert result.success is False
    mock_memory.store.assert_not_awaited()


async def test_create_disabled_by_capture_config_returns_named_error(mock_memory, mock_embedder):
    capture = CaptureSettings(enabled=False)
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, capture=capture)

    result = await tool.execute(operation="create", content="algo")

    assert result.success is False
    assert "memories.capture.enabled" in result.output
    mock_memory.store.assert_not_awaited()


def test_create_not_in_enum_when_capture_disabled(mock_memory, mock_embedder):
    capture = CaptureSettings(enabled=False)
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, capture=capture)

    assert "create" not in tool.parameters_schema["properties"]["operation"]["enum"]


def test_create_in_enum_when_capture_enabled(mock_memory, mock_embedder):
    capture = CaptureSettings(enabled=True)
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder, capture=capture)

    assert "create" in tool.parameters_schema["properties"]["operation"]["enum"]


def test_description_mentions_memory_policy_only_when_capture_enabled():
    enabled_tool = _make_tool(capture=CaptureSettings(enabled=True))
    disabled_tool = _make_tool(capture=CaptureSettings(enabled=False))

    assert MEMORY_POLICY in enabled_tool.description
    assert MEMORY_POLICY not in disabled_tool.description


def test_description_mentions_search_history_and_knowledge_search():
    tool = _make_tool()

    assert "search_history" in tool.description
    assert "knowledge_search" in tool.description


# ---------------------------------------------------------------------------
# despacho / operación desconocida
# ---------------------------------------------------------------------------


async def test_unknown_operation_lists_valid_ones(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="wipe_everything")

    assert result.success is False
    assert "search" in result.output
    assert "list" in result.output
    assert "update" in result.output
    assert "delete" in result.output


async def test_missing_operation_is_unknown(mock_memory, mock_embedder):
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute()

    assert result.success is False


async def test_unexpected_exception_is_caught_and_logged(mock_memory, mock_embedder, caplog):
    mock_memory.search_with_scores.side_effect = RuntimeError("boom")
    tool = _make_tool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(operation="search", query="algo")

    assert result.success is False
    assert "boom" in result.output
    # El catch genérico de execute() es retryable=True: no distingue de las
    # excepciones transitorias del repo/embedder que atrapaban las tools
    # viejas, y contarlo como no-retryable dispararía antes el circuit
    # breaker del tool loop.
    assert result.retryable is True


# ---------------------------------------------------------------------------
# invariante outbound-send-single-owner: sin referencias a history
# ---------------------------------------------------------------------------


def test_tool_has_no_history_reference():
    import inspect

    source = inspect.getsource(MemoryTool)
    assert "history" not in source.lower()
